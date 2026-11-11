"""Files, CAS claims and upload quota a pull request holds, and their release."""

import logging
import typing

from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import ConflictError
from app.core.database.post_commit import PostCommitKey
from app.core.database.redis import RedisClient
from app.models.cas_staging_claim import CasStagingClaim
from app.models.pull_request import PRFileClaim, PullRequest
from app.models.upload import Upload

logger = logging.getLogger(__name__)


def get_pr_staging_files(pr: PullRequest) -> list[str]:
    """Collect all 'uploads/' prefixed file keys from a PR payload."""
    staging_files: list[str] = []
    for op in pr.payload:
        fk = op.get("file_key")
        if fk and str(fk).startswith("uploads/"):
            staging_files.append(str(fk))
        attachments = op.get("attachments")
        if isinstance(attachments, list):
            for att in attachments:
                att_fk = att.get("file_key") if isinstance(att, dict) else None
                if att_fk and str(att_fk).startswith("uploads/"):
                    staging_files.append(str(att_fk))
    return staging_files


def get_pr_all_file_keys(pr: PullRequest) -> list[str]:
    """Collect all file keys from a PR payload, regardless of prefix."""
    keys: list[str] = []
    for op in pr.payload:
        fk = op.get("file_key")
        if fk:
            keys.append(str(fk))
        attachments = op.get("attachments")
        if isinstance(attachments, list):
            for att in attachments:
                att_fk = att.get("file_key") if isinstance(att, dict) else None
                if att_fk:
                    keys.append(str(att_fk))
    return keys


async def _lock_and_validate_pr_cas_files(
    db: AsyncSession,
    pr: PullRequest,
    *,
    settled_statuses: set[str] | frozenset[str] | None = None,
) -> list[Upload]:
    """Lock and validate every CAS key claimed by a contribution.

    A key is eligible when it is backed by an author-owned clean upload with an
    active staging reference, or by a consumed generated-CAS claim. Duplicate
    upload rows cannot satisfy a different missing key because validation is set
    based. Callers hold these row locks through approval.
    """
    expected_keys = {key for key in get_pr_all_file_keys(pr) if key.startswith("cas/")}
    if not expected_keys:
        return []

    claimed_keys = set(
        await db.scalars(
            select(PRFileClaim.file_key)
            .where(PRFileClaim.pr_id == pr.id, PRFileClaim.file_key.in_(expected_keys))
            .with_for_update()
        )
    )
    if claimed_keys != expected_keys:
        raise ConflictError("Contribution file claims changed; reload and retry")

    upload_rows = list(
        (
            await db.scalars(
                select(Upload)
                .where(
                    Upload.final_key.in_(expected_keys),
                    Upload.user_id == pr.author_id,
                )
                .with_for_update()
            )
        ).all()
    )
    eligible_keys = {
        upload.final_key
        for upload in upload_rows
        if upload.final_key
        and upload.status == "clean"
        and int(upload.cas_ref_count or 0) > 0
        and (settled_statuses is None or upload.processing_status in settled_statuses)
    }

    consumed_claim_keys = set(
        await db.scalars(
            select(CasStagingClaim.file_key)
            .where(
                CasStagingClaim.user_id == pr.author_id,
                CasStagingClaim.file_key.in_(expected_keys),
                CasStagingClaim.consumed_at.is_not(None),
            )
            .with_for_update()
        )
    )
    eligible_keys.update(consumed_claim_keys)
    if eligible_keys != expected_keys:
        raise ConflictError("One or more contribution files are no longer clean, settled, or owned")
    return upload_rows


async def _release_pr_upload_quota(
    db: AsyncSession,
    pr: PullRequest,
    redis: Redis,
    approved: bool = False,  # type: ignore[type-arg]
) -> list[str]:
    """Queue quota cleanup and return the selected uploads' reservation IDs.

    Approval makes the legacy object durable, so its reservation is released by
    a separate post-commit job after invalidating usage. Rejection cleanup
    couples reservation release to successful staging-object deletion.
    """
    keys = get_pr_all_file_keys(pr)
    if not keys:
        return []

    # A CAS key may have many duplicate Upload rows. Select one author-owned
    # staging owner per claimed key deterministically; never mutate another
    # user's duplicate upload.
    stmt = (
        select(Upload)
        .where(
            (Upload.quarantine_key.in_(keys)) | (Upload.final_key.in_(keys)),
            Upload.user_id == pr.author_id,
        )
        .order_by(Upload.updated_at.desc(), Upload.created_at.desc())
        .with_for_update()
    )
    result = await db.execute(stmt)
    candidate_uploads = list(result.scalars().all())
    candidates_by_key: dict[str, list[Upload]] = {}
    for upload in candidate_uploads:
        matched_key = upload.final_key if upload.final_key in keys else upload.quarantine_key
        if matched_key:
            candidates_by_key.setdefault(matched_key, []).append(upload)
    uploads: list[Upload] = []
    for claimed_key in keys:
        candidates = candidates_by_key.get(claimed_key, [])
        if not candidates:
            continue
        selected = candidates[0]
        if approved:
            selected = next(
                (
                    upload
                    for upload in candidates
                    if upload.status == "clean"
                    and (not claimed_key.startswith("cas/") or int(upload.cas_ref_count or 0) > 0)
                ),
                selected,
            )
        uploads.append(selected)

    if not uploads:
        return []

    keys_to_remove: list[str] = []
    reservation_ids: list[str] = []

    for upload in uploads:
        if upload.quarantine_key:
            keys_to_remove.append(upload.quarantine_key)
        keys_to_remove.append(f"staging:{upload.user_id}:{upload.upload_id}")
        reservation_ids.append(upload.upload_id)

        if approved and upload.status == "clean":
            upload.status = "applied"

    if keys_to_remove:
        db.info.setdefault(PostCommitKey.JOBS, []).append(
            ("release_upload_quota", str(pr.author_id), sorted(set(keys_to_remove)))
        )

    return sorted(set(reservation_ids))


async def _cleanup_pr_resources(
    db: AsyncSession,
    pr: PullRequest,
    delete_staging: bool = False,
    redis: RedisClient | None = None,
) -> None:
    """Release file claims and optionally schedule deletion of staging files."""
    await db.execute(delete(PRFileClaim).where(PRFileClaim.pr_id == pr.id))

    # Select affected uploads and queue external cleanup in the transaction outbox.
    if redis is None:
        from app.core.database.redis import redis_client

        redis = redis_client

    reservation_ids: list[str] = []
    if redis:  # type: ignore[truthy-bool]
        # If delete_staging is False, it's likely an approval
        reservation_ids = await _release_pr_upload_quota(
            db,
            pr,
            redis,
            approved=not delete_staging,
        )

    # CAS staging refs need the original SHA-256, but PR payload hashes are
    # optional/untrusted. Resolve release identities from authoritative Upload
    # rows or consumed generated-CAS claims instead.
    cas_keys = {key for key in get_pr_all_file_keys(pr) if key.startswith("cas/")}
    if cas_keys:
        upload_rows = list(
            (
                await db.scalars(
                    select(Upload)
                    .where(
                        Upload.final_key.in_(cas_keys),
                        Upload.user_id == pr.author_id,
                    )
                    .order_by(Upload.updated_at.desc(), Upload.created_at.desc())
                    .with_for_update()
                )
            ).all()
        )
        claim_rows = list(
            (
                await db.scalars(
                    select(CasStagingClaim)
                    .where(
                        CasStagingClaim.user_id == pr.author_id,
                        CasStagingClaim.file_key.in_(cas_keys),
                        CasStagingClaim.consumed_at.is_not(None),
                    )
                    .with_for_update()
                )
            ).all()
        )

        all_uploads_by_key: dict[str, list[Upload]] = {}
        uploads_by_key: dict[str, Upload] = {}
        for upload_row in upload_rows:
            if upload_row.final_key:
                all_uploads_by_key.setdefault(upload_row.final_key, []).append(upload_row)
            if (
                upload_row.final_key
                and upload_row.final_key not in uploads_by_key
                and upload_row.status in ("clean", "applied")
                and int(upload_row.cas_ref_count or 0) > 0
            ):
                uploads_by_key[upload_row.final_key] = upload_row
        claims_by_key = {claim.file_key: claim for claim in claim_rows}

        refs_to_release: list[str] = []
        for key in sorted(cas_keys):
            upload = uploads_by_key.get(key)
            if upload is not None:
                reference_sha = upload.content_sha256 or upload.sha256
                if not reference_sha:
                    logger.error(
                        "Cannot release CAS staging reference for %s: authoritative hash missing",
                        key,
                    )
                    continue
                refs_to_release.append(reference_sha)
                upload.cas_ref_count = 0
                if delete_staging and upload.status == "clean":
                    upload.status = "failed"
                continue

            # If Upload rows exist but none owns a live CAS ref, do not release a
            # second owner for the same key. Claim fallback is for generated-CAS
            # files that genuinely have no Upload owner.
            if key in all_uploads_by_key:
                continue
            claim = claims_by_key.get(key)
            if claim is not None:
                refs_to_release.append(claim.sha256)
            else:
                logger.warning(
                    "No authoritative CAS staging owner found while cleaning PR %s key %s",
                    pr.id,
                    key,
                )

        if refs_to_release:
            db.info.setdefault(PostCommitKey.JOBS, []).append(
                (
                    "release_cas_references",
                    [
                        {
                            "sha256": sha256,
                            "operation_id": f"pr:{pr.id}:staging:{sha256}:release",
                        }
                        for sha256 in sorted(set(refs_to_release))
                    ],
                )
            )

    uploads_to_delete = get_pr_staging_files(pr)
    if uploads_to_delete:
        job: tuple[typing.Any, ...] = (
            "delete_storage_objects",
            uploads_to_delete,
            reservation_ids,
        )
        if not delete_staging:
            # Approval already copied uploads/ bytes to durable materials/.
            # Release capacity only after the source object is actually gone,
            # and fence stale legacy-usage snapshots in that same worker step.
            job = (*job, True)
        db.info.setdefault(PostCommitKey.JOBS, []).append(job)
