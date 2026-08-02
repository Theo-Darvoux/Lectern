"""ARQ cron task: abort orphaned S3 multipart uploads.

An upload is considered orphaned when:
- it exists in S3 under the quarantine/ prefix, AND
- no live tus:state:* Redis key references its S3 upload ID, AND
- the uploads DB table has no row with status 'pending' or 'processing' for that upload_id, AND
- the multipart upload was initiated more than 2 hours ago.
"""

import logging
from datetime import UTC, datetime

from app.core.storage.facade import abort_multipart_upload, list_multipart_uploads

logger = logging.getLogger(__name__)

_ORPHAN_AGE_SECONDS = 7200  # 2 hours


async def reconcile_multipart_uploads(ctx: dict) -> None:  # type: ignore[type-arg]
    redis = ctx["redis"]
    session_factory = ctx.get("db_sessionmaker")

    # 1. Build set of active S3 upload IDs from all live tus sessions (Issue O9)
    active_s3_ids: set[str] = set()
    from app.routers.tus import _TUS_ACTIVE_SESSIONS, _TUS_STATE_PREFIX

    tus_ids = await redis.smembers(_TUS_ACTIVE_SESSIONS)
    for tid_bytes in tus_ids:
        tid = tid_bytes.decode() if isinstance(tid_bytes, bytes) else tid_bytes
        state_key = f"{_TUS_STATE_PREFIX}{tid}"
        s3_id = await redis.hget(state_key, "s3_upload_id")
        if s3_id:
            active_s3_ids.add(s3_id.decode() if isinstance(s3_id, bytes) else s3_id)
        else:
            # Clean up stale ID from set if state is gone
            await redis.srem(_TUS_ACTIVE_SESSIONS, tid)  # type: ignore[unused-ignore]

    # 2. The DB is authoritative for live upload ownership. Never perform
    # destructive reconciliation when that protection set cannot be loaded.
    if session_factory is None:
        logger.error("reconcile_multipart: DB session factory unavailable; skipping")
        return
    try:
        from sqlalchemy import select

        from app.models.upload import Upload

        async with session_factory() as session:
            rows = await session.scalars(
                select(Upload.upload_id).where(Upload.status.in_(("pending", "processing")))
            )
            live_upload_ids = {str(r) for r in rows}
    except Exception as exc:
        logger.error("reconcile_multipart: DB query failed; skipping destructive pass: %s", exc)
        return

    # 3. List all in-progress multipart uploads under quarantine/
    aborted = 0
    skipped = 0
    async for mp in list_multipart_uploads(prefix="quarantine/"):
        s3_upload_id = mp["UploadId"]
        initiated: datetime = mp["Initiated"]
        s3_key: str = mp["Key"]

        # Must be older than 2 hours
        age = (datetime.now(UTC) - initiated).total_seconds()
        if age < _ORPHAN_AGE_SECONDS:
            skipped += 1
            continue

        # Skip if a live tus session references this S3 upload
        if s3_upload_id in active_s3_ids:
            skipped += 1
            continue

        # Extract upload_id from key: quarantine/{user_id}/{upload_id}/{filename}
        parts = s3_key.split("/")
        upload_id = parts[2] if len(parts) >= 4 else None

        # Skip if DB shows this upload is still pending or processing.
        if upload_id and upload_id in live_upload_ids:
            skipped += 1
            continue

        try:
            await abort_multipart_upload(s3_key, s3_upload_id)
            aborted += 1
            logger.info(
                "reconcile_multipart: aborted orphan s3_upload_id=%s key=%s age=%.0fs",
                s3_upload_id,
                s3_key,
                age,
            )
        except Exception as exc:
            logger.warning("reconcile_multipart: abort failed for %s: %s", s3_upload_id, exc)

    logger.info("reconcile_multipart: done — aborted=%d skipped=%d", aborted, skipped)
