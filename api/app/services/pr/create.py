"""Create a pull request, auto-applying it when the author may self-merge."""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.exceptions import BadRequestError, ConflictError
from app.core.database.post_commit import PostCommitKey
from app.core.database.redis import RedisClient
from app.core.storage.facade import object_exists
from app.models.cas_staging_claim import CasStagingClaim
from app.models.material import Material
from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
from app.models.security import VirusScanResult
from app.models.upload import Upload
from app.models.user import User
from app.schemas.pull_request import PullRequestCreate
from app.services.pr.apply import apply_pr
from app.services.pr.operations import _assert_temp_refs_defined, _pr_directory_topics
from app.services.pr.resources import _cleanup_pr_resources, _lock_and_validate_pr_cas_files
from app.services.reaction_lock import acquire_reaction_toggle_lock

# Upload processing statuses after which a deferred auto-merge may proceed.
_AUTO_MERGE_SETTLED_STATUSES = frozenset({"complete", "degraded"})


async def create_pull_request_service(
    db: AsyncSession,
    data: PullRequestCreate,
    current_user: User,
    redis: RedisClient | None = None,
) -> PullRequest:
    """Validate and create a new batch pull request."""
    is_privileged = current_user.is_moderator

    _assert_temp_refs_defined([op.model_dump(mode="json") for op in data.operations])

    if not is_privileged:
        if len(data.operations) > settings.pr_max_ops_student:
            raise BadRequestError(
                f"You can include at most {settings.pr_max_ops_student} changes per contribution"
            )
        for op in data.operations:
            if (
                getattr(op, "op", None) == "create_material"
                and len(getattr(op, "attachments", [])) > settings.pr_max_attachments_per_material
            ):
                raise BadRequestError(
                    f"You can add at most {settings.pr_max_attachments_per_material} attachments per document"
                )
    else:
        if len(data.operations) > settings.pr_max_ops_staff:
            raise BadRequestError(
                f"You can include at most {settings.pr_max_ops_staff} changes per contribution"
            )

    # Serialize count+insert for one non-staff author. A cardinality limit cannot
    # be expressed as a uniqueness constraint, so the admission decision needs a
    # transaction-scoped per-user serialization point.
    if not current_user.is_staff:
        await acquire_reaction_toggle_lock(
            db,
            kind="user-pr-create",
            user_id=current_user.id,
            target_id=current_user.id,
        )
        open_count = await db.scalar(
            select(func.count())
            .select_from(PullRequest)
            .where(
                PullRequest.author_id == current_user.id,
                PullRequest.status == PRStatus.OPEN,
            )
        )
        if open_count and open_count >= settings.pr_max_open_per_user:
            raise BadRequestError(
                f"You already have {settings.pr_max_open_per_user} contributions pending review. "
                "Wait for one to be reviewed before submitting another."
            )

    max_bytes = settings.max_file_size_mb * 1024 * 1024

    for op in data.operations:
        ds = getattr(op, "diff_summary", None)
        if ds and len(ds.encode("utf-8")) > max_bytes:
            raise BadRequestError(
                f"The changes in one of your files are too large ({len(ds.encode('utf-8'))} bytes). "
                f"The current limit is {max_bytes // (1024 * 1024)} MB."
            )

    # Validate file_key ownership.
    user_upload_prefix = f"uploads/{current_user.id}/"
    cas_prefix = "cas/"
    keys_to_check: set[str] = set()
    declared_hashes: dict[str, str] = {}
    for op in data.operations:
        file_key = getattr(op, "file_key", None)
        if file_key:
            if not (file_key.startswith(user_upload_prefix) or file_key.startswith(cas_prefix)):
                raise BadRequestError("One of the attached files does not belong to your account")
            keys_to_check.add(file_key)
            content_sha = getattr(op, "content_sha256", None)
            if content_sha:
                declared_hashes[file_key] = str(content_sha)

        if getattr(op, "op", None) == "create_material":
            for att in getattr(op, "attachments", []):
                att_fk = (
                    att.file_key
                    if hasattr(att, "file_key")
                    else (att.get("file_key") if isinstance(att, dict) else None)
                )
                if att_fk:
                    if not (att_fk.startswith(user_upload_prefix) or att_fk.startswith(cas_prefix)):
                        raise BadRequestError(
                            "One of the attachment files does not belong to your account"
                        )
                    keys_to_check.add(att_fk)
                    att_sha = (
                        getattr(att, "content_sha256", None)
                        if not isinstance(att, dict)
                        else att.get("content_sha256")
                    )
                    if att_sha:
                        declared_hashes[att_fk] = str(att_sha)

            pmid = getattr(op, "parent_material_id", None)
            if pmid:
                actual_pmid: uuid.UUID | None = None
                if isinstance(pmid, uuid.UUID):
                    actual_pmid = pmid
                elif isinstance(pmid, str) and not pmid.startswith("$"):
                    with contextlib.suppress(ValueError):
                        actual_pmid = uuid.UUID(pmid)

                if actual_pmid:
                    parent_mat = await db.scalar(select(Material).where(Material.id == actual_pmid))
                    if parent_mat and parent_mat.parent_material_id is not None:
                        raise BadRequestError("Cannot attach a material to another attachment")

    if keys_to_check:
        # Every key must be backed by either this user's clean Upload row or an
        # unexpired, single-use generated-CAS claim.
        owned_upload_rows = list(
            (
                await db.scalars(
                    select(Upload)
                    .where(
                        (Upload.final_key.in_(keys_to_check))
                        | (Upload.quarantine_key.in_(keys_to_check)),
                        Upload.user_id == current_user.id,
                    )
                    .with_for_update()
                )
            ).all()
        )
        upload_rows = [upload for upload in owned_upload_rows if upload.status == "clean"]
        authorized_hashes: dict[str, str] = {}
        for upload in upload_rows:
            authorized_key = (
                upload.final_key if upload.final_key in keys_to_check else upload.quarantine_key
            )
            if authorized_key:
                if authorized_key.startswith("cas/"):
                    if (
                        int(upload.cas_ref_count or 0) <= 0
                        or upload.size_bytes is None
                        or not upload.mime_type
                        or upload.mime_type == "application/octet-stream"
                        or not upload.content_sha256
                    ):
                        continue
                    authorized_hashes[authorized_key] = upload.content_sha256
                else:
                    # Legacy uploads are revalidated against S3 during promotion.
                    authorized_hashes[authorized_key] = upload.content_sha256 or upload.sha256 or ""

        now = datetime.now(UTC)
        claims = list(
            (
                await db.scalars(
                    select(CasStagingClaim)
                    .where(
                        CasStagingClaim.user_id == current_user.id,
                        CasStagingClaim.file_key.in_(keys_to_check),
                        CasStagingClaim.consumed_at.is_(None),
                        CasStagingClaim.expires_at > now,
                    )
                    .with_for_update()
                )
            ).all()
        )
        claims_by_key = {claim.file_key: claim for claim in claims}
        for claim in claims:
            authorized_hashes.setdefault(claim.file_key, claim.sha256)

        def upload_label(key: str) -> str:
            matching_rows = [
                row
                for row in owned_upload_rows
                if row.final_key == key or row.quarantine_key == key
            ]
            filename = next((row.filename for row in matching_rows if row.filename), None)
            return f'The file "{filename}"' if filename else "This staged file"

        for key in sorted(keys_to_check):
            expected_sha = authorized_hashes.get(key)
            if expected_sha is None:
                matching_rows = [
                    row
                    for row in owned_upload_rows
                    if row.final_key == key or row.quarantine_key == key
                ]
                statuses = {row.status for row in matching_rows}
                label = upload_label(key)

                if "malicious" in statuses:
                    scanner_reason = next(
                        (
                            row.error_detail
                            for row in matching_rows
                            if row.status == "malicious" and row.error_detail
                        ),
                        None,
                    )
                    reason_text = (
                        " Scanner reason: "
                        f"{scanner_reason.removeprefix('ERR_MALWARE_DETECTED: ').strip().rstrip('.')}."
                        if scanner_reason
                        else ""
                    )
                    raise BadRequestError(
                        f"{label} was blocked because a malware scan flagged it as potentially "
                        f"unsafe.{reason_text} Remove it from this contribution and upload a safe "
                        "replacement."
                    )
                if statuses & {"pending", "processing"}:
                    raise BadRequestError(
                        f"{label} is still being checked for safety. Wait for processing to finish "
                        "before submitting."
                    )
                if "failed" in statuses:
                    raise BadRequestError(
                        f"{label} could not be verified because processing failed. Remove it from "
                        "this contribution and upload it again."
                    )
                if matching_rows:
                    raise BadRequestError(
                        f"{label} is no longer available for submission. Remove it from this "
                        "contribution and upload it again."
                    )
                raise BadRequestError(
                    "This staged file is not available for your account. Remove it from this "
                    "contribution and upload it again."
                )
            declared_sha = declared_hashes.get(key)
            if (
                declared_sha is not None
                and key.startswith(cas_prefix)
                and declared_sha != expected_sha
            ):
                raise BadRequestError("A CAS file hash does not match its verified upload claim")

        existence_results = await asyncio.gather(*(object_exists(k) for k in keys_to_check))
        for key, exists in zip(keys_to_check, existence_results, strict=False):
            if not exists:
                raise BadRequestError(
                    f"{upload_label(key)} could not be found. Remove it from this contribution "
                    "and upload it again."
                )

        for key, claim in claims_by_key.items():
            if key not in {row.final_key for row in upload_rows}:
                claim.consumed_at = now

    # Serialize operations to list[dict]
    ops_payload = [op.model_dump(mode="json") for op in data.operations]
    summary_types = sorted({op.op for op in data.operations})

    has_file = any(
        op_dict.get("file_key")
        or any(
            isinstance(att, dict) and att.get("file_key")
            for att in (op_dict.get("attachments") or [])
        )
        for op_dict in ops_payload
    )

    pr = PullRequest(
        id=uuid.uuid4(),
        type="batch",
        status=PRStatus.OPEN,
        title=data.title,
        description=data.description,
        payload=ops_payload,
        summary_types=summary_types,
        author_id=current_user.id,
        is_anonymous=data.is_anonymous,
        virus_scan_result=VirusScanResult.CLEAN if has_file else VirusScanResult.SKIPPED,
    )
    db.add(pr)
    await db.flush()

    # Claim file keys atomically via DB unique constraint.
    if keys_to_check:
        for fk in keys_to_check:
            db.add(PRFileClaim(file_key=fk, pr_id=pr.id))
        try:
            await db.flush()
        except IntegrityError:
            raise BadRequestError(
                "One or more files are already included in another pending contribution. "
                "Please wait for that contribution to be reviewed first."
            )

    # Auto-approve for privileged users if their setting is enabled.
    # If any uploaded file is still being post-processed (processing_status not settled),
    # defer the merge: create the PR as OPEN with auto_merge_pending=True.
    # process_upload_post_scan will trigger the merge once all files settle.
    if current_user.is_moderator and current_user.auto_approve:
        # Use the exact eligibility check the deferred auto-merge applies. Counting
        # every Upload row sharing a CAS key let stale duplicates (cancelled uploads,
        # other users' copies) defer the merge forever: their post-scan never settles,
        # so nothing ever re-triggers it. Ownership and cleanliness were validated
        # above, so a conflict here only means a file is still being post-processed.
        all_settled = True
        try:
            await _lock_and_validate_pr_cas_files(
                db, pr, settled_statuses=_AUTO_MERGE_SETTLED_STATUSES
            )
        except ConflictError:
            all_settled = False

        if all_settled:
            pr.status = PRStatus.APPROVED
            pr.reviewed_by = current_user.id
            await apply_pr(db, pr)
            await _cleanup_pr_resources(db, pr, redis=redis)

            broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
            closed_event = {"type": "pr_closed", "id": str(pr.id)}
            for topic in _pr_directory_topics(list(pr.payload)):
                broadcasts.append((topic, closed_event))
            if pr.author_id is not None:
                broadcasts.append((f"pr_updates:{pr.author_id}", closed_event))
        else:
            # Files still processing — hold the PR open and merge once they settle.
            pr.auto_merge_pending = True

    await db.refresh(pr, ["author", "created_at", "updated_at"])

    if pr.status == PRStatus.OPEN:
        broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
        event = {"type": "pr_opened", "id": str(pr.id)}
        for topic in _pr_directory_topics(list(pr.payload)):
            broadcasts.append((topic, event))
        if pr.author_id is not None:
            broadcasts.append((f"pr_updates:{pr.author_id}", event))

    return pr
