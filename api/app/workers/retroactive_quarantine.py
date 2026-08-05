"""Retroactive quarantine: delete a promoted file that was flagged by MalwareBazaar.

Called by ``check_bazaar`` when a post-promotion Bazaar lookup returns a threat hit.
By the time this runs, the file may be in any of these states:

1. Still in ``cas/`` only (staging, not yet approved into ``materials/``).
2. Already referenced by an approved ``MaterialVersion`` row.

This module handles both cases defensively:

* DB Upload row → status = "malicious"
* CAS ref count decremented; S3 object deleted when ref_count reaches 0
* Any ``MaterialVersion`` rows referencing the cas key → soft-deleted (if enabled)
* SSE event emitted so the uploader's browser can update in real time
* Quota slot released (zrem from ``quota:uploads:{user_id}``)

All operations are idempotent: repeated calls for the same upload_id are no-ops.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func as sa_func
from sqlalchemy import select, update

from app.config import settings
from app.core.database.post_commit import (
    PostCommitKey,
    dispatch_post_commit_actions,
    persist_post_commit_jobs,
)
from app.core.database.redis import redis_lock
from app.models.material import Material, MaterialVersion
from app.models.upload import Upload
from app.routers.upload.cancellation import upload_lifecycle_lock_name
from app.schemas.material import UploadStatus
from app.workers.upload.context import WorkerContext

logger = logging.getLogger(__name__)

# Redis key prefix for the quota tracking sorted set (mirrors upload worker logic).
_QUOTA_KEY_PREFIX = "quota:uploads:"
# Redis prefix for upload event log / channel (mirrors pipeline.py).
_STATUS_KEY_PREFIX = "upload:status:"
_EVENT_CHANNEL_PREFIX = "upload:events:"
_EVENT_LOG_PREFIX = "upload:eventlog:"


async def retroactive_quarantine(
    ctx: WorkerContext,
    *,
    upload_id: str,
    sha256: str,
    cas_s3_key: str,
    user_id: str,
    threat: str,
) -> None:
    """Mark a promoted upload malicious under the shared lifecycle lock."""
    session_factory = ctx.db_sessionmaker
    redis = ctx.redis

    if session_factory is None:
        raise RuntimeError("retroactive quarantine requires a database session factory")

    logger.warning(
        "retroactive_quarantine: processing upload %s (sha256=%.16s…, threat=%s)",
        upload_id,
        sha256,
        threat,
    )

    async with redis_lock(
        cast(Any, redis),
        upload_lifecycle_lock_name(upload_id),
        timeout=120.0,
        expire=300.0,
    ):
        async with session_factory() as session:
            session.info[PostCommitKey.JOBS] = []
            row: Upload | None = await session.scalar(
                select(Upload).where(Upload.upload_id == upload_id).with_for_update()
            )
            if row is not None and row.status in ("failed", "deleted"):
                logger.info(
                    "retroactive_quarantine: upload %s already in terminal state '%s', skipping.",
                    upload_id,
                    row.status,
                )
                return

            quarantine_key: str | None = row.quarantine_key if row else None
            already_malicious = row is not None and row.status == "malicious"
            if row is not None:
                if not already_malicious:
                    row.status = "malicious"
                    row.error_detail = f"MalwareBazaar retroactive hit: {threat}"
                    row.updated_at = datetime.now(UTC)
                # Reconcile cleanup even when a previous attempt committed the
                # malicious status first. Stable operation IDs make retries safe.
                if row.cas_ref_count > 0:
                    reference_sha = row.content_sha256 or sha256
                    session.info[PostCommitKey.JOBS].append(
                        (
                            "release_cas_references",
                            [
                                {
                                    "sha256": reference_sha,
                                    "operation_id": f"quarantine:upload:{upload_id}:release",
                                }
                            ],
                        )
                    )
                    row.cas_ref_count = 0
                if row.thumbnail_key:
                    if _thumbnail_owned_by_upload(row.thumbnail_key, upload_id):
                        session.info[PostCommitKey.JOBS].append(
                            ("delete_storage_objects", [row.thumbnail_key])
                        )
                    row.thumbnail_key = None
                    row.thumbnail_status = "failed"

            # A retry after the malicious DB commit must still repair derived
            # material state and Redis publication. CAS release operations are
            # idempotent, so repeating this reconciliation is safe.
            if settings.bazaar_retroactive_check_materials:
                await _quarantine_material_versions(session, sha256, cas_s3_key, threat)

            if not already_malicious or session.info[PostCommitKey.JOBS]:
                await persist_post_commit_jobs(session)
                await session.commit()
                await dispatch_post_commit_actions(session)

        # Cache/event publication is part of the same lifecycle critical section,
        # so a post-scan worker cannot append CLEAN after this MALICIOUS event.
        await _emit_malicious_sse(redis, upload_id, threat, quarantine_key)

        staging_key = f"staging:{user_id}:{upload_id}"
        try:
            await redis.zrem(f"{_QUOTA_KEY_PREFIX}{user_id}", staging_key)
        except Exception as exc:
            logger.warning(
                "retroactive_quarantine: quota cleanup failed for %s: %s", upload_id, exc
            )

    logger.info("retroactive_quarantine: completed for upload %s (threat=%s).", upload_id, threat)


def _thumbnail_owned_by_upload(thumbnail_key: str, upload_id: str) -> bool:
    """Return true only for upload-specific thumbnail keys.

    Legacy ``thumbnails/<cas-id>.webp`` keys are shared and must never be
    deleted as compensation for a single upload.
    """
    return thumbnail_key.endswith(f"/{upload_id}.webp")


async def _quarantine_material_versions(
    session: Any,
    sha256: str,
    cas_s3_key: str,
    threat: str,
) -> None:
    """Soft-delete any MaterialVersion rows whose file resolves to this cas key.

    Matching logic: ``MaterialVersion.cas_sha256 == sha256`` OR
    ``MaterialVersion.file_key == cas_s3_key`` (covers both direct and CAS references).
    """
    now = datetime.now(UTC)
    # Find affected versions
    versions: list[MaterialVersion] = list(
        (
            await session.scalars(
                select(MaterialVersion).where(
                    (MaterialVersion.cas_sha256 == sha256)
                    | (MaterialVersion.file_key == cas_s3_key),
                    MaterialVersion.deleted_at.is_(None),
                )
            )
        ).all()
    )

    if not versions:
        return

    version_ids = [str(v.id) for v in versions]
    material_ids = list({str(v.material_id) for v in versions})
    logger.warning(
        "retroactive_quarantine: soft-deleting %d MaterialVersion row(s) "
        "for threat=%s (version_ids=%s, material_ids=%s)",
        len(versions),
        threat,
        version_ids,
        material_ids,
    )

    # Soft-delete each version and durably release its own material reference.
    for v in versions:
        v.deleted_at = now
        if v.cas_sha256 and v.file_key and v.file_key.startswith("cas/"):
            session.info[PostCommitKey.JOBS].append(
                (
                    "release_cas_references",
                    [
                        {
                            "sha256": v.cas_sha256,
                            "operation_id": f"quarantine:version:{v.id}:release",
                        }
                    ],
                )
            )
    await session.flush()

    # Soft-delete any parent Material that has *no* surviving live versions.
    for mid_str in material_ids:
        try:
            mid = uuid.UUID(mid_str)
        except ValueError:
            continue
        live_count: int = (
            await session.scalar(
                select(sa_func.count())
                .select_from(MaterialVersion)
                .where(
                    MaterialVersion.material_id == mid,
                    MaterialVersion.deleted_at.is_(None),
                )
            )
        ) or 0
        if live_count == 0:
            await session.execute(
                update(Material)
                .where(Material.id == mid, Material.deleted_at.is_(None))
                .values(deleted_at=now)
            )
            logger.warning(
                "retroactive_quarantine: soft-deleted Material %s (all versions were malicious).",
                mid_str,
            )


async def _emit_malicious_sse(
    redis: Any,
    upload_id: str,
    threat: str,
    quarantine_key: str | None,
) -> None:
    """Push a 'malicious' SSE event so the uploader's browser can react in real time."""
    if quarantine_key is None:
        return

    status_key = f"{_STATUS_KEY_PREFIX}{quarantine_key}"
    event_channel = f"{_EVENT_CHANNEL_PREFIX}{quarantine_key}"
    event_log_key = f"{_EVENT_LOG_PREFIX}{quarantine_key}"

    payload: dict[str, Any] = {
        "upload_id": upload_id,
        "file_key": quarantine_key,
        "status": UploadStatus.MALICIOUS,
        "detail": f"File flagged by MalwareBazaar: {threat}",
        "result": None,
    }
    payload_json = json.dumps(payload)

    try:
        await redis.set(status_key, payload_json, ex=3600)
        idx = await redis.rpush(event_log_key, payload_json)
        if idx == 1:
            await redis.expire(event_log_key, 7200)
        elif idx > 200:
            await redis.ltrim(event_log_key, -200, -1)
        await redis.publish(event_channel, payload_json)
    except Exception as exc:
        logger.warning("retroactive_quarantine: SSE emit failed for upload %s: %s", upload_id, exc)
        raise
