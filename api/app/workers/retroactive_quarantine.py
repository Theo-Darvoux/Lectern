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
from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import select, update

from app.config import settings
from app.core.database.post_commit import (
    PostCommitKey,
    dispatch_post_commit_actions,
    persist_post_commit_jobs,
)
from app.models.material import Material, MaterialVersion
from app.models.upload import Upload
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
    """Mark a promoted upload as malicious and clean up all associated resources.

    Args:
        ctx:        Worker context (Redis + DB session factory).
        upload_id:  The internal upload identifier.
        sha256:     Original file SHA-256 (used for CAS ref decrement).
        cas_s3_key: The ``cas/<id>`` S3 object key.
        user_id:    Uploader's UUID string (for quota cleanup).
        threat:     Threat name returned by MalwareBazaar (for error_detail).
    """
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

    # Serialize against post-scan publication and persist every destructive CAS
    # reference change in the same transaction as the malicious status.
    async with session_factory() as session:
        session.info[PostCommitKey.JOBS] = []
        row: Upload | None = await session.scalar(
            select(Upload).where(Upload.upload_id == upload_id).with_for_update()
        )
        if row is not None and row.status in ("malicious", "failed", "deleted"):
            logger.info(
                "retroactive_quarantine: upload %s already in terminal state '%s', skipping.",
                upload_id,
                row.status,
            )
            return

        quarantine_key: str | None = row.quarantine_key if row else None
        if row is not None:
            row.status = "malicious"
            row.error_detail = f"MalwareBazaar retroactive hit: {threat}"
            row.updated_at = datetime.now(UTC)
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
                session.info[PostCommitKey.JOBS].append(
                    ("delete_storage_objects", [row.thumbnail_key])
                )
                row.thumbnail_key = None
                row.thumbnail_status = "failed"

        if settings.bazaar_retroactive_check_materials:
            await _quarantine_material_versions(session, sha256, cas_s3_key, threat)

        await persist_post_commit_jobs(session)
        await session.commit()
        await dispatch_post_commit_actions(session)

    # ── 5. Emit SSE event to notify the uploader's browser ───────────────────
    await _emit_malicious_sse(redis, upload_id, threat, quarantine_key)

    # ── 6. Release quota slot ─────────────────────────────────────────────────
    staging_key = f"staging:{user_id}:{upload_id}"
    try:
        await redis.zrem(f"{_QUOTA_KEY_PREFIX}{user_id}", staging_key)
    except Exception as exc:
        logger.warning("retroactive_quarantine: quota cleanup failed for %s: %s", upload_id, exc)

    logger.info("retroactive_quarantine: completed for upload %s (threat=%s).", upload_id, threat)


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
