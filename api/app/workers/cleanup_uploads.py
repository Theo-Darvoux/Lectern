import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("wikint")


async def cleanup_uploads(ctx: dict) -> None:  # type: ignore[type-arg]
    logger.info("Running upload cleanup cron job")
    from sqlalchemy import select, update

    from app.config import settings
    from app.core.database import async_session_factory
    from app.core.storage import get_s3_client
    from app.models.pull_request import PRStatus, PullRequest

    # ── 1. Expire old Pull Requests (7 days) ─────────────────────────────────
    pr_cutoff = datetime.now(UTC) - timedelta(days=7)

    async with async_session_factory() as db:
        expire_stmt = (
            update(PullRequest)
            .where(PullRequest.status == PRStatus.OPEN)
            .where(PullRequest.updated_at < pr_cutoff)
            .values(status=PRStatus.REJECTED)
        )
        from typing import cast

        from sqlalchemy.engine import CursorResult

        res = cast(CursorResult, await db.execute(expire_stmt))  # type: ignore[type-arg]
        await db.commit()
        if res.rowcount > 0:
            logger.info("Expired %d stale Pull Requests (older than 7 days)", res.rowcount)

    # ── 1c. Abort stale Multipart Uploads (24 hours) ─────────────────────────
    from app.core.storage import abort_multipart_upload, list_multipart_uploads

    mp_cutoff = datetime.now(UTC) - timedelta(hours=24)
    mp_aborted = 0

    async for mp in list_multipart_uploads():
        initiated = mp["Initiated"]
        if isinstance(initiated, datetime) and initiated < mp_cutoff:
            await abort_multipart_upload(cast(str, mp["Key"]), cast(str, mp["UploadId"]))
            mp_aborted += 1

    if mp_aborted > 0:
        logger.info("Aborted %d stale S3 multipart uploads (older than 24h)", mp_aborted)

    # ── 1b. Expire stale pending Uploads (2 hours) ───────────────────────────
    pending_cutoff = datetime.now(UTC) - timedelta(hours=2)

    async with async_session_factory() as db:
        from app.models.upload import Upload

        pending_stmt = (
            update(Upload)
            .where(Upload.status == "pending")
            .where(Upload.created_at < pending_cutoff)
            .values(status="failed", error_detail="Upload never completed (timed out)")
        )
        pending_res = cast(CursorResult, await db.execute(pending_stmt))  # type: ignore[type-arg]
        await db.commit()
        if pending_res.rowcount > 0:
            logger.info("Expired %d stale pending uploads (older than 2h)", pending_res.rowcount)

    # ── 2. Collect protected keys ────────────────────────────────────────────
    protected_keys: set[str] = set()
    async with async_session_factory() as db:
        result = await db.execute(
            select(PullRequest).where(PullRequest.status.in_([PRStatus.OPEN, PRStatus.APPROVED]))
        )
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

    # ── 3. Clean terminal uploads ────────────────────────────────────────────
    # CAS V2: terminal uploads reference cas/ keys. We decrement the CAS ref
    # instead of deleting S3 objects (which are shared).
    orphan_cutoff = datetime.now(UTC) - timedelta(hours=48)
    quarantine_cutoff = datetime.now(UTC) - timedelta(hours=2)

    non_cas_to_delete: list[str] = []
    cas_refs_to_decrement: list[str] = []  # SHA-256 values

    async with async_session_factory() as db:
        from app.models.upload import Upload

        terminal_statuses = ["clean", "failed", "malicious", "applied"]
        upload_result = await db.execute(
            select(Upload).where(
                Upload.status.in_(terminal_statuses),
                Upload.updated_at < orphan_cutoff,
            )
        )
        terminal_uploads: list[Upload] = list(upload_result.scalars().all())

    for upload in terminal_uploads:
        key = upload.final_key or upload.quarantine_key
        if not key or key in protected_keys:
            continue

        if key.startswith("cas/"):
            # CAS V2: decrement ref count instead of deleting shared object.
            # Use the upload's original SHA-256 for proper ref counting.
            if upload.sha256:
                cas_refs_to_decrement.append(upload.sha256)
        else:
            # Legacy V1 keys (uploads/, quarantine/) — direct S3 delete
            non_cas_to_delete.append(key)

    # Also clean up the synthetic staging quota entries
    redis = ctx["redis"]
    for upload in terminal_uploads:
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

        await delete_storage_objects(ctx, non_cas_to_delete)
        logger.info("Cleanup triggered for %d staging/quarantine objects", len(non_cas_to_delete))

    if cas_refs_to_decrement:
        from app.core.cas import decrement_cas_ref

        for sha256 in cas_refs_to_decrement:
            await decrement_cas_ref(redis, sha256)
        logger.info("Decremented CAS refs for %d expired uploads", len(cas_refs_to_decrement))

    # ── 4. Clean orphaned cas/ objects ───────────────────────────────────────
    # CAS objects without a Redis ref entry are orphans. The 48h safety margin
    # prevents deleting objects that are mid-upload or mid-finalize.
    from sqlalchemy import select

    from app.models.material import MaterialVersion

    async with async_session_factory() as db:
        # Collect all active legacy file_keys to prevent deleting valid production data
        result = await db.execute(
            select(MaterialVersion.file_key).where(
                MaterialVersion.file_key.is_not(None), MaterialVersion.file_key.not_like("cas/%")
            )
        )
        valid_legacy_keys = {row[0] for row in result if row[0]}

    orphans_to_delete: list[str] = []

    async with get_s3_client() as client:
        paginator = client.get_paginator("list_objects_v2")

        cas_keys_raw = await redis.keys("upload:cas:*")
        valid_cas_ids = {
            (k.decode() if isinstance(k, bytes) else k).split(":")[-1] for k in cas_keys_raw
        }

        async for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix="cas/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                cas_id = key.split("/")[-1]
                if cas_id in valid_cas_ids:
                    continue
                if obj["LastModified"].replace(tzinfo=None) < orphan_cutoff.replace(tzinfo=None):
                    orphans_to_delete.append(key)

        # Legacy: clean remaining materials/ or uploads/ objects that are NOT in valid_legacy_keys
        for prefix in ("materials/", "uploads/"):
            async for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key in valid_legacy_keys:
                        continue
                    if obj["LastModified"].replace(tzinfo=None) < orphan_cutoff.replace(
                        tzinfo=None
                    ):
                        orphans_to_delete.append(key)

    if orphans_to_delete:
        from app.workers.storage_ops import delete_storage_objects

        await delete_storage_objects(ctx, orphans_to_delete)
        logger.info("Cleanup triggered for %d orphaned objects", len(orphans_to_delete))
    else:
        logger.info("No orphaned objects found to clean up")

    # ── 5. Integrity: verify CAS objects referenced by MaterialVersions exist ─
    # If a CAS object is missing from S3 but still referenced in the DB, log
    # a warning. We do NOT delete the DB row automatically — this requires
    # manual investigation.
    from app.core.storage import object_exists
    from app.models.material import MaterialVersion

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
