"""Upload shared helpers: quota enforcement, DB row creation, job enqueueing."""

import logging
import time
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.redis as redis_core
from app.core.common.exceptions import BadRequestError
from app.core.common.upload_errors import UploadErrorCode
from app.core.database.post_commit import PostCommitKey, outbox_kwargs
from app.core.observability.telemetry import inject_trace_context
from app.core.storage import capacity as storage_capacity
from app.models.upload import Upload

logger = logging.getLogger(__name__)

# Backward-compatible router aliases. New non-router callers use the public
# core storage interface directly.
_LEGACY_STORAGE_USAGE_KEY = storage_capacity.LEGACY_STORAGE_USAGE_KEY
_check_storage_limit = storage_capacity.check_storage_limit
_get_storage_usage = storage_capacity.get_storage_usage
_refresh_legacy_storage_usage = storage_capacity.refresh_legacy_storage_usage
_release_storage_reservation = storage_capacity.release_storage_reservation
_reserve_storage_limit = storage_capacity.reserve_storage_limit

MAX_PENDING_UPLOADS = 50
LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50 MiB
LARGE_SVG_THRESHOLD = LARGE_FILE_THRESHOLD  # alias used by tests

_FAST_QUEUE_THRESHOLD = 5 * 1024 * 1024  # 5 MiB
_FAST_QUEUE_NAME = "upload-fast"
_SLOW_QUEUE_NAME = "upload-slow"

_QUOTA_KEY_PREFIX = "quota:uploads:"
_IDEM_KEY_PREFIX = "upload:idem:"
_IDEM_TTL = 25 * 3600  # 25 h -- slightly longer than the 24 h file TTL
_UPLOAD_INTENT_PREFIX = "upload:intent:"
_UPLOAD_INTENT_TTL = 3600  # 1 h to complete a presigned upload
_STATUS_CACHE_PREFIX = "upload:status:"


async def _create_upload_row(
    upload_id: str,
    user_id: str,
    quarantine_key: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    db: AsyncSession,
    status: str = "pending",
) -> None:
    """Persist an upload lifecycle row. Mandatory: raises on failure."""
    row = Upload(
        upload_id=upload_id,
        user_id=UUID(user_id),
        quarantine_key=quarantine_key,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        status=status,
    )
    db.add(row)
    await db.flush()


async def _check_pending_cap(
    user_id: str,
    redis: "Redis",  # type: ignore[type-arg]
    db: AsyncSession,
    *,
    privileged: bool = False,
    reserve_key: str | None = None,
) -> None:
    """Raise if the user has hit the pending-upload ceiling.

    Privileged users (moderators, bureau, vieux) are exempt from the count cap
    and only subject to the global storage limit. The quota key is still written
    so cleanup workers can track and expire their uploads normally.

    Optionally reserves ``reserve_key`` atomically to prevent TOCTOU races.
    Fail-closed: if Redis is unreachable, we reject the upload.
    """
    quota_key = f"{_QUOTA_KEY_PREFIX}{user_id}"
    try:
        cutoff = time.time() - (25 * 3600)
        await redis.zremrangebyscore(quota_key, "-inf", cutoff)

        if reserve_key:
            await redis.zadd(quota_key, {reserve_key: time.time()})

        if privileged:
            return

        cap = MAX_PENDING_UPLOADS
        if reserve_key:
            count = await redis.zcard(quota_key)
            if count > cap:
                await redis.zrem(quota_key, reserve_key)
                raise BadRequestError(
                    f"Too many pending uploads ({cap} max). "
                    "Submit a pull request or wait for existing uploads to expire.",
                    code=UploadErrorCode.QUOTA_EXCEEDED,
                )
        else:
            count = await redis.zcard(quota_key)
            if count >= cap:
                raise BadRequestError(
                    f"Too many pending uploads ({cap} max). "
                    "Submit a pull request or wait for existing uploads to expire.",
                    code=UploadErrorCode.QUOTA_EXCEEDED,
                )
    except BadRequestError:
        raise
    except Exception as exc:
        if privileged:
            return
        logger.warning(
            "Redis quota check failed for %s -- falling back to DB count: %s",
            user_id,
            exc,
        )
        # Fallback: count pending rows in DB (degraded mode — no atomic reservation).
        # Use a fresh session to avoid PendingRollbackError if the request session
        # is in an error state from a previous operation.
        try:
            from datetime import UTC, datetime, timedelta

            from app.core.database.database import async_session_factory

            db_cutoff = datetime.now(UTC) - timedelta(hours=25)
            async with async_session_factory() as fallback_db:
                db_count = (
                    await fallback_db.scalar(
                        select(func.count())
                        .select_from(Upload)
                        .where(
                            Upload.user_id == UUID(user_id),
                            Upload.status.in_(("pending", "clean")),
                            Upload.updated_at >= db_cutoff,
                        )
                    )
                    or 0
                )

            if db_count >= MAX_PENDING_UPLOADS:
                raise BadRequestError(
                    f"Too many pending uploads ({MAX_PENDING_UPLOADS} max). "
                    "Submit a pull request or wait for existing uploads to expire.",
                    code=UploadErrorCode.QUOTA_EXCEEDED,
                )
        except BadRequestError:
            raise
        except Exception as db_exc:
            logger.error(
                "DB quota fallback also failed for %s -- rejecting upload: %s",
                user_id,
                db_exc,
            )
            raise BadRequestError(
                "Service temporarily unavailable (quota check failed). Please try again later."
            )


async def _enqueue_processing(
    user_id: str,
    upload_id: str,
    quarantine_key: str,
    filename: str,
    mime_type: str,
    *,
    file_size: int = 0,
    trace_context: dict[str, str] | None = None,
    expected_sha256: str | None = None,
    job_id: str | None = None,
) -> None:
    """Enqueue the background processing ARQ job.

    Routes to the fast queue for files below ``_FAST_QUEUE_THRESHOLD`` so that
    small document uploads are never blocked by large video transcode jobs.
    """
    if redis_core.arq_pool is None:
        raise BadRequestError("Background processing is temporarily unavailable. Please try again.")

    queue_name = _processing_queue_name(mime_type, file_size)

    tc = trace_context if trace_context is not None else inject_trace_context()
    job_options: dict[str, object] = {"_queue_name": queue_name}
    if job_id is not None:
        job_options["_job_id"] = job_id
    enqueue_job = cast(Any, redis_core.arq_pool.enqueue_job)
    await enqueue_job(
        "process_upload",
        **job_options,
        user_id=user_id,
        upload_id=upload_id,
        quarantine_key=quarantine_key,
        original_filename=filename,
        mime_type=mime_type,
        expected_sha256=expected_sha256,
        trace_context=tc,
    )


def _processing_queue_name(mime_type: str, file_size: int) -> str:
    """Choose the ARQ queue used for an upload's processing job."""
    is_fast_mime = any(mime_type.startswith(m) for m in ("text/", "image/"))
    is_heavy_mime = any(
        mime_type.startswith(m)
        for m in (
            "video/",
            "application/pdf",
            "application/zip",
            "application/x-zip-compressed",
            "application/epub+zip",
            "application/vnd.openxmlformats-officedocument",
        )
    )

    if is_fast_mime and not is_heavy_mime:
        queue_name = _FAST_QUEUE_NAME
    elif is_heavy_mime:
        queue_name = _SLOW_QUEUE_NAME
    else:
        # Fallback to size-based routing for unknown/mixed types
        queue_name = _FAST_QUEUE_NAME if file_size < _FAST_QUEUE_THRESHOLD else _SLOW_QUEUE_NAME

    return queue_name


def _queue_processing_after_commit(
    db: AsyncSession,
    user_id: str,
    upload_id: str,
    quarantine_key: str,
    filename: str,
    mime_type: str,
    *,
    file_size: int = 0,
    trace_context: dict[str, str] | None = None,
    expected_sha256: str | None = None,
) -> None:
    """Persist an upload-processing job in the request transaction's outbox."""
    tc = trace_context if trace_context is not None else inject_trace_context()
    queue_name = _processing_queue_name(mime_type, file_size)
    db.info.setdefault(PostCommitKey.JOBS, []).append(
        (
            "process_upload",
            outbox_kwargs(
                _queue_name=queue_name,
                user_id=user_id,
                upload_id=upload_id,
                quarantine_key=quarantine_key,
                original_filename=filename,
                mime_type=mime_type,
                expected_sha256=expected_sha256,
                trace_context=tc,
            ),
        )
    )
