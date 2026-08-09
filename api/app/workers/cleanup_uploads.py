import logging
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import delete, select, update

from app.config import settings
from app.core.database.database import async_session_factory
from app.core.database.post_commit import (
    PostCommitKey,
    dispatch_post_commit_actions,
    persist_post_commit_jobs,
)
from app.core.storage.capacity import reconcile_cas_storage_usage, release_storage_reservation
from app.core.storage.facade import abort_multipart_upload, get_s3_client, list_multipart_uploads
from app.models.cas_staging_claim import CasStagingClaim
from app.models.material import MaterialVersion
from app.models.pull_request import PRStatus, PullRequest
from app.models.upload import Upload

logger = logging.getLogger(__name__)


async def _release_expired_cas_material_versions(
    db,
    redis,
    revert_cutoff: datetime,
) -> int:
    """Release one durable CAS ref per expired soft-deleted version, then reap it."""
    from app.core.security.cas import CasReferenceMissingError, decrement_cas_ref

    versions = list(
        (
            await db.scalars(
                select(MaterialVersion)
                .where(
                    MaterialVersion.file_key.like("cas/%"),
                    MaterialVersion.deleted_at.is_not(None),
                    MaterialVersion.deleted_at < revert_cutoff,
                )
                .execution_options(include_deleted=True)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    released = 0
    for version in versions:
        sha256 = version.cas_sha256
        if not sha256:
            logger.error(
                "Cannot release expired CAS MaterialVersion %s: cas_sha256 is missing",
                version.id,
            )
            continue
        try:
            await decrement_cas_ref(
                redis,
                sha256,
                operation_id=f"cleanup:material-version:{version.id}:expire",
            )
        except CasReferenceMissingError:
            # Redis CAS refs are an evictable coordination cache. A missing ref
            # means there is no live ref to leak; the expired DB owner can go.
            logger.warning(
                "CAS reference already absent while reaping expired MaterialVersion %s",
                version.id,
            )
        except Exception as exc:
            logger.error(
                "Failed to release CAS reference for expired MaterialVersion %s: %s",
                version.id,
                exc,
            )
            continue
        await db.delete(version)
        released += 1
    return released


async def _reap_expired_legacy_material_key(
    db,
    ctx: dict,  # type: ignore[type-arg]
    key: str,
    revert_cutoff: datetime,
    *,
    object_seen: bool,
) -> bool:
    """Delete an expired legacy object before removing its capacity-owning rows.

    Rows are locked across the object-store delete so a concurrent revert cannot
    resurrect a MaterialVersion after its bytes have been removed.
    """
    versions = list(
        (
            await db.scalars(
                select(MaterialVersion)
                .where(MaterialVersion.file_key == key)
                .execution_options(include_deleted=True)
                .with_for_update()
            )
        ).all()
    )
    if not versions:
        return False

    for version in versions:
        deleted_at = version.deleted_at
        if deleted_at is None:
            return True
        if deleted_at.tzinfo is None:
            deleted_at = deleted_at.replace(tzinfo=UTC)
        if deleted_at >= revert_cutoff:
            return True

    from app.core.storage.facade import object_exists
    from app.workers.storage_ops import delete_storage_objects

    if object_seen or await object_exists(key):
        await delete_storage_objects(ctx, [key])

    for version in versions:
        await db.delete(version)
    await db.commit()
    return True


async def cleanup_uploads(ctx: dict) -> None:  # type: ignore[type-arg]
    logger.info("Running upload cleanup cron job")

    # ── 1. Expire stale pending Uploads (2 hours) ────────────────────────────
    pending_cutoff = datetime.now(UTC) - timedelta(hours=2)

    async with async_session_factory() as db:
        db.info[PostCommitKey.JOBS] = []
        pending_stmt = (
            update(Upload)
            .where(Upload.status == "pending")
            .where(Upload.created_at < pending_cutoff)
            .values(status="failed", error_detail="Upload never completed (timed out)")
            .returning(Upload.upload_id, Upload.user_id, Upload.quarantine_key)
        )
        pending_rows = list((await db.execute(pending_stmt)).all())
        if pending_rows:
            logger.info("Expired %d stale pending uploads (older than 2h)", len(pending_rows))

        now = datetime.now(UTC)
        expired_claims = list(
            (
                await db.scalars(
                    select(CasStagingClaim)
                    .where(
                        CasStagingClaim.expires_at < now,
                        CasStagingClaim.consumed_at.is_(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        if expired_claims:
            db.info[PostCommitKey.JOBS].append(
                (
                    "release_cas_references",
                    [
                        {
                            "sha256": claim.sha256,
                            "operation_id": f"qcm-claim:{claim.id}:expire",
                        }
                        for claim in expired_claims
                    ],
                )
            )
        await db.execute(
            delete(CasStagingClaim).where(
                (CasStagingClaim.expires_at < now)
                | (CasStagingClaim.consumed_at < now - timedelta(days=7))
            )
        )
        await persist_post_commit_jobs(db)
        await db.commit()
        await dispatch_post_commit_actions(db)

        from app.routers.upload.helpers import _QUOTA_KEY_PREFIX

        for upload_id, user_id, quarantine_key in pending_rows:
            await release_storage_reservation(upload_id, ctx["redis"])
            await ctx["redis"].zrem(f"{_QUOTA_KEY_PREFIX}{user_id}", quarantine_key)

    # ── 2. Expire old Pull Requests (7 days) ─────────────────────────────────
    pr_cutoff = datetime.now(UTC) - timedelta(days=7)

    async with async_session_factory() as db:
        from app.services.pr import _cleanup_pr_resources

        db.info[PostCommitKey.JOBS] = []
        stale_prs = list(
            (
                await db.scalars(
                    select(PullRequest)
                    .where(
                        PullRequest.status == PRStatus.OPEN,
                        PullRequest.updated_at < pr_cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for pr in stale_prs:
            pr.status = PRStatus.REJECTED
            await _cleanup_pr_resources(db, pr, delete_staging=True, redis=ctx["redis"])
        await persist_post_commit_jobs(db)
        await db.commit()
        await dispatch_post_commit_actions(db)
        if stale_prs:
            logger.info("Expired %d stale Pull Requests (older than 7 days)", len(stale_prs))

    # ── 3. Abort stale Multipart Uploads (24 hours) ──────────────────────────
    mp_cutoff = datetime.now(UTC) - timedelta(hours=24)
    mp_aborted = 0

    async for mp in list_multipart_uploads():
        initiated = mp["Initiated"]
        if isinstance(initiated, datetime) and initiated < mp_cutoff:
            await abort_multipart_upload(cast(str, mp["Key"]), cast(str, mp["UploadId"]))
            mp_aborted += 1

    if mp_aborted > 0:
        logger.info("Aborted %d stale S3 multipart uploads (older than 24h)", mp_aborted)

    # ── 4. Collect protected keys ────────────────────────────────────────────
    protected_keys: set[str] = set()
    async with async_session_factory() as db:
        result = await db.execute(select(PullRequest).where(PullRequest.status == PRStatus.OPEN))
        for pr in result.scalars():
            payload = cast(list[dict], pr.payload)  # type: ignore[type-arg]
            for op in payload:
                fk = op.get("file_key")
                if fk:
                    protected_keys.add(fk)
                attachments = cast(list[dict], op.get("attachments", []))  # type: ignore[type-arg]
                for att in attachments:
                    att_fk = att.get("file_key")
                    if att_fk:
                        protected_keys.add(att_fk)

    # ── 5. Clean terminal uploads ────────────────────────────────────────────
    # CAS V2: terminal uploads reference cas/ keys. We decrement the CAS ref
    # instead of deleting S3 objects (which are shared).
    orphan_cutoff = datetime.now(UTC) - timedelta(hours=48)
    quarantine_cutoff = datetime.now(UTC) - timedelta(hours=2)

    non_cas_to_delete: list[str] = []
    non_cas_reservation_ids: list[str] = []
    quota_uploads_to_release: list[Upload] = []
    async with async_session_factory() as db:
        db.info[PostCommitKey.JOBS] = []
        terminal_statuses = ["clean", "failed", "malicious", "applied"]
        upload_result = await db.execute(
            select(Upload)
            .where(
                Upload.status.in_(terminal_statuses),
                Upload.updated_at < orphan_cutoff,
            )
            .with_for_update(skip_locked=True)
        )
        terminal_uploads: list[Upload] = list(upload_result.scalars().all())

        release_refs: list[dict[str, str]] = []
        for upload in terminal_uploads:
            key = upload.final_key or upload.quarantine_key
            if not key or key in protected_keys:
                continue

            if key.startswith("cas/"):
                reference_sha = upload.content_sha256 or upload.sha256
                if reference_sha and upload.cas_ref_count > 0:
                    release_refs.append(
                        {
                            "sha256": reference_sha,
                            "operation_id": f"cleanup:upload:{upload.id}:release",
                        }
                    )
                    upload.cas_ref_count = 0
            else:
                non_cas_to_delete.append(key)
                non_cas_reservation_ids.append(upload.upload_id)
            quota_uploads_to_release.append(upload)

        if release_refs:
            db.info[PostCommitKey.JOBS].append(("release_cas_references", release_refs))
        await persist_post_commit_jobs(db)
        await db.commit()
        await dispatch_post_commit_actions(db)

    # Also clean up the synthetic staging quota entries
    redis = ctx["redis"]
    for upload in quota_uploads_to_release:
        staging_key = f"staging:{upload.user_id}:{upload.upload_id}"
        await redis.zrem(f"quota:uploads:{upload.user_id}", staging_key)

    # Clean quarantine/ files older than quarantine_cutoff via S3 scan.
    async with get_s3_client() as client:
        paginator = client.get_paginator("list_objects_v2")

        async for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix="quarantine/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if obj["LastModified"].replace(tzinfo=None) < quarantine_cutoff.replace(
                    tzinfo=None
                ):
                    non_cas_to_delete.append(key)

    if non_cas_to_delete:
        from app.workers.storage_ops import delete_storage_objects

        # Terminal uploads have one reservation per object. Quarantine objects
        # discovered only by S3 scan are appended later and have no reservation.
        terminal_count = len(non_cas_reservation_ids)
        if terminal_count:
            await delete_storage_objects(
                ctx,
                non_cas_to_delete[:terminal_count],
                non_cas_reservation_ids,
            )
        if len(non_cas_to_delete) > terminal_count:
            await delete_storage_objects(ctx, non_cas_to_delete[terminal_count:])
        logger.info("Cleanup triggered for %d staging/quarantine objects", len(non_cas_to_delete))

    # ── 6. Clean orphaned cas/ objects ───────────────────────────────────────
    # CAS objects without a Redis ref entry are orphans. The 48h safety margin
    # prevents deleting objects that are mid-upload or mid-finalize.
    revert_cutoff = datetime.now(UTC) - timedelta(days=settings.pr_revert_grace_days)
    async with async_session_factory() as db:
        released_expired_cas = await _release_expired_cas_material_versions(
            db, ctx["redis"], revert_cutoff
        )
        if released_expired_cas:
            await db.commit()
            logger.info(
                "Released and reaped %d expired CAS MaterialVersion(s)",
                released_expired_cas,
            )

        legacy_result = await db.execute(
            select(MaterialVersion.file_key)
            .where(
                MaterialVersion.file_key.is_not(None),
                MaterialVersion.file_key.not_like("cas/%"),
                (MaterialVersion.deleted_at.is_(None))
                | (MaterialVersion.deleted_at >= revert_cutoff),
            )
            .execution_options(include_deleted=True)
        )
        valid_legacy_keys = {row[0] for row in legacy_result if row[0]}

        expired_legacy_result = await db.execute(
            select(MaterialVersion.file_key)
            .where(
                MaterialVersion.file_key.like("materials/%"),
                MaterialVersion.deleted_at.is_not(None),
                MaterialVersion.deleted_at < revert_cutoff,
            )
            .execution_options(include_deleted=True)
        )
        expired_legacy_keys = {row[0] for row in expired_legacy_result if row[0]}

        material_cas_result = await db.execute(
            select(MaterialVersion.file_key)
            .where(
                MaterialVersion.file_key.like("cas/%"),
                (MaterialVersion.deleted_at.is_(None))
                | (MaterialVersion.deleted_at >= revert_cutoff),
            )
            .execution_options(include_deleted=True)
        )
        valid_cas_keys = {row[0] for row in material_cas_result if row[0]}

        # Upload rows own a CAS reference until cleanup marks cas_ref_count=0.
        # Recent rows are also protected during finalization even if the count
        # has not yet been persisted.
        upload_cas_result = await db.execute(
            select(Upload.final_key).where(
                Upload.final_key.like("cas/%"),
                (Upload.cas_ref_count > 0) | (Upload.updated_at >= orphan_cutoff),
            )
        )
        valid_cas_keys.update(row[0] for row in upload_cas_result if row[0])

    orphans_to_delete: list[str] = []
    legacy_material_candidates: list[str] = []
    seen_legacy_material_keys: set[str] = set()

    async with get_s3_client() as client:
        paginator = client.get_paginator("list_objects_v2")

        valid_cas_ids = {key.split("/", 1)[1] for key in valid_cas_keys}
        async for cas_key in redis.scan_iter("upload:cas:*"):
            k = cas_key.decode() if isinstance(cas_key, bytes) else cas_key
            valid_cas_ids.add(k.split(":")[-1])

        async for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix="cas/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                cas_id = key.split("/")[-1]
                if cas_id in valid_cas_ids:
                    continue
                if obj["LastModified"].replace(tzinfo=None) < orphan_cutoff.replace(tzinfo=None):
                    orphans_to_delete.append(key)

        # Legacy: clean remaining materials/ or uploads/ objects that are NOT in valid_legacy_keys.
        # Expired materials/ keys with DB owners are handled separately under row locks
        # so reverts serialize against physical deletion.
        for prefix in ("materials/", "uploads/"):
            async for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if prefix == "materials/":
                        seen_legacy_material_keys.add(key)
                    if key in valid_legacy_keys or key in protected_keys:
                        continue
                    if obj["LastModified"].replace(tzinfo=None) < orphan_cutoff.replace(
                        tzinfo=None
                    ):
                        if prefix == "materials/" and key in expired_legacy_keys:
                            legacy_material_candidates.append(key)
                        else:
                            orphans_to_delete.append(key)

    # Reap expired legacy MaterialVersions only after their physical bytes are
    # confirmed absent/deleted. Capacity continues counting the rows until then.
    for key in sorted(set(legacy_material_candidates)):
        async with async_session_factory() as db:
            handled = await _reap_expired_legacy_material_key(
                db, ctx, key, revert_cutoff, object_seen=True
            )
        if not handled:
            orphans_to_delete.append(key)

    for key in sorted(expired_legacy_keys - seen_legacy_material_keys - protected_keys):
        async with async_session_factory() as db:
            await _reap_expired_legacy_material_key(db, ctx, key, revert_cutoff, object_seen=False)

    if orphans_to_delete:
        from app.core.storage.facade import delete_object
        from app.workers.storage_ops import delete_storage_objects

        cas_orphans = [key for key in orphans_to_delete if key.startswith("cas/")]
        legacy_orphans = [key for key in orphans_to_delete if not key.startswith("cas/")]
        # These CAS objects are proven absent from both authoritative DB refs
        # and Redis's cache; do not attempt to re-HMAC their opaque object IDs.
        for key in cas_orphans:
            await delete_object(key)
        if legacy_orphans:
            await delete_storage_objects(ctx, legacy_orphans)
        logger.info("Cleanup triggered for %d orphaned objects", len(orphans_to_delete))
    else:
        logger.info("No orphaned objects found to clean up")

    # Redis ref records and aggregate usage are evictable coordination caches.
    # Rebuild the physical CAS total with a generation fence so a concurrent
    # CAS creation cannot be overwritten by a stale object-store scan.
    try:
        await reconcile_cas_storage_usage(redis)
    except Exception as exc:
        logger.warning("Failed to reconcile physical CAS usage: %s", exc)

    # ── 7. Integrity: verify CAS objects referenced by MaterialVersions exist ─
    # If a CAS object is missing from S3 but still referenced in the DB, log
    # a warning. We do NOT delete the DB row automatically — this requires
    # manual investigation.
    from app.core.storage.facade import object_exists

    async with async_session_factory() as db:
        result = await db.execute(
            select(MaterialVersion.file_key).where(
                MaterialVersion.file_key.is_not(None),
                MaterialVersion.file_key.like("cas/%"),
            )
        )
        cas_file_keys = {row[0] for row in result if row[0]}

    missing_count = 0
    for fk in cas_file_keys:
        if not await object_exists(fk):
            logger.warning("CAS object missing from S3 but referenced by MaterialVersion: %s", fk)
            missing_count += 1

    if missing_count > 0:
        logger.warning("Found %d MaterialVersion(s) referencing missing CAS objects", missing_count)
    else:
        logger.info("All CAS-backed MaterialVersions verified")
