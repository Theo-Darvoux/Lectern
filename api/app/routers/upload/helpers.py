"""Upload shared helpers: quota enforcement, DB row creation, job enqueueing."""

import logging
import time
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

import app.core.redis as redis_core
from app.config import settings
from app.core.database import async_session_factory
from app.core.exceptions import BadRequestError
from app.core.upload_errors import ERR_QUOTA_EXCEEDED, ERR_STORAGE_FULL

logger = logging.getLogger("wikint")


async def _check_storage_limit(size_bytes: int, config: dict[str, Any]) -> None:
    """Raise if the global storage limit (max_storage_gb) would be exceeded."""
    from sqlalchemy import func, select

    from app.core.cas import _STORAGE_USAGE_KEY
    from app.models.material import MaterialVersion

    max_gb = (
        config.get("max_storage_gb")
        if config.get("max_storage_gb") is not None
        else settings.max_storage_gb
    )
    if not max_gb:
        return

    max_bytes = max_gb * 1024 * 1024 * 1024
    redis = redis_core.redis_client

    # 1. Try to get cached usage from Redis
    try:
        usage_raw = await redis.get(_STORAGE_USAGE_KEY)
        if usage_raw is not None:
            usage = max(0, int(usage_raw))
        else:
            # 2. Fallback to DB: Sum unique CAS blobs only (Physical Storage)
            async with async_session_factory() as session:
                # We subquery to get the size of each unique CAS blob
                # and sum them up to get the physical storage usage.
                from sqlalchemy import select

                inner_q = (
                    select(func.max(MaterialVersion.file_size).label("size"))
                    .group_by(MaterialVersion.cas_sha256)
                    .subquery()
                )
                usage = await session.scalar(select(func.sum(inner_q.c.size))) or 0

            # Cache the result for 1 hour
            await redis.set(_STORAGE_USAGE_KEY, usage, ex=3600)
    except Exception as exc:
        logger.warning(
            "Failed to get/set storage usage from Redis: %s. Falling back to logical sum.", exc
        )
        # Deep fallback to logical sum if everything else fails
        async with async_session_factory() as session:
            usage = await session.scalar(select(func.sum(MaterialVersion.file_size))) or 0

    if usage + size_bytes > max_bytes:
        logger.warning(
            "Storage limit reached: %d bytes usage + %d bytes upload > %d bytes limit",
            usage,
            size_bytes,
            max_bytes,
        )
        raise BadRequestError(
            f"Global storage limit reached ({max_gb} GB). Please contact an administrator.",
            code=ERR_STORAGE_FULL,
        )


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
    status: str = "pending",
) -> None:
    """Persist an upload lifecycle row. Mandatory: raises on failure."""
    from app.models.upload import Upload

    async with async_session_factory() as session:
        row = Upload(
            upload_id=upload_id,
            user_id=UUID(user_id),
            quarantine_key=quarantine_key,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            status=status,
        )
        session.add(row)
        await session.commit()


async def _check_pending_cap(
    user_id: str,
    redis: "Redis",  # type: ignore[type-arg]
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
                    code=ERR_QUOTA_EXCEEDED,
                )
        else:
            count = await redis.zcard(quota_key)
            if count >= cap:
                raise BadRequestError(
                    f"Too many pending uploads ({cap} max). "
                    "Submit a pull request or wait for existing uploads to expire.",
                    code=ERR_QUOTA_EXCEEDED,
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
        # Fallback: count pending rows in DB (degraded mode — no atomic reservation)
        try:
            from sqlalchemy import func, select

            from app.models.upload import Upload

            async with async_session_factory() as _db:
                db_count = (
                    await _db.scalar(
                        select(func.count())
                        .select_from(Upload)
                        .where(Upload.user_id == UUID(user_id), Upload.status == "pending")
                    )
                    or 0
                )
            if db_count >= MAX_PENDING_UPLOADS:
                raise BadRequestError(
                    f"Too many pending uploads ({MAX_PENDING_UPLOADS} max). "
                    "Submit a pull request or wait for existing uploads to expire.",
                    code=ERR_QUOTA_EXCEEDED,
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
) -> None:
    """Enqueue the background processing ARQ job.

    Routes to the fast queue for files below ``_FAST_QUEUE_THRESHOLD`` so that
    small document uploads are never blocked by large video transcode jobs.
    """
    from app.core.telemetry import inject_trace_context

    if redis_core.arq_pool is None:
        raise BadRequestError("Background processing is temporarily unavailable. Please try again.")

    # Priority-based queue routing
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

    tc = trace_context if trace_context is not None else inject_trace_context()
    await redis_core.arq_pool.enqueue_job(
        "process_upload",
        _queue_name=queue_name,
        user_id=user_id,
        upload_id=upload_id,
        quarantine_key=quarantine_key,
        original_filename=filename,
        mime_type=mime_type,
        expected_sha256=expected_sha256,
        trace_context=tc,
    )
