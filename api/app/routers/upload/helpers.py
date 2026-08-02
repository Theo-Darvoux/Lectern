"""Upload shared helpers: quota enforcement, DB row creation, job enqueueing."""

import logging
import time
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.redis as redis_core
from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.core.common.upload_errors import UploadErrorCode
from app.core.database.post_commit import PostCommitKey, outbox_kwargs
from app.core.observability.telemetry import inject_trace_context
from app.core.security.cas import _STORAGE_USAGE_KEY
from app.models.material import MaterialVersion
from app.models.upload import Upload

logger = logging.getLogger(__name__)

_LUA_DIR = Path(__file__).parents[2] / "core" / "database" / "lua"
_STORAGE_RESERVE_SCRIPT = (_LUA_DIR / "storage_reserve.lua").read_text(encoding="utf-8")
_STORAGE_RELEASE_SCRIPT = (_LUA_DIR / "storage_release.lua").read_text(encoding="utf-8")
_STORAGE_RESERVATION_EXPIRIES = "storage:upload_reservations:expiries"
_STORAGE_RESERVATION_SIZES = "storage:upload_reservations:sizes"
_STORAGE_RESERVATION_TOTAL = "storage:upload_reservations:total"
_STORAGE_RESERVATION_TTL = 3 * 3600


async def _get_storage_usage(db: AsyncSession, redis: "Redis") -> int:  # type: ignore[type-arg]
    """Return physical CAS usage, rebuilding the cache from the database if needed."""
    material_refs = select(
        MaterialVersion.cas_sha256.label("sha256"),
        MaterialVersion.file_size.label("size"),
    ).where(MaterialVersion.cas_sha256.is_not(None))
    upload_refs = select(
        Upload.content_sha256.label("sha256"),
        Upload.size_bytes.label("size"),
    ).where(
        Upload.content_sha256.is_not(None),
        Upload.final_key.like("cas/%"),
        Upload.cas_ref_count > 0,
    )
    all_refs = union_all(material_refs, upload_refs).subquery()
    unique_sizes = (
        select(func.max(all_refs.c.size).label("size"))
        .group_by(all_refs.c.sha256)
        .subquery()
    )

    async def _from_db() -> int:
        return int(await db.scalar(select(func.sum(unique_sizes.c.size))) or 0)

    try:
        usage_raw = await redis.get(_STORAGE_USAGE_KEY)
    except Exception as exc:
        logger.warning("Storage usage cache unavailable; using the database: %s", exc)
        return await _from_db()
    if usage_raw is not None:
        return max(0, int(usage_raw))

    usage = await _from_db()
    try:
        # Do not overwrite a CAS increment that raced the database rebuild.
        # SET NX makes initialization atomic with the Lua CAS writers; read the
        # winning value back when another writer initialized it first.
        initialized = await redis.set(_STORAGE_USAGE_KEY, usage, nx=True)
        if not initialized:
            current = await redis.get(_STORAGE_USAGE_KEY)
            if current is not None:
                return max(0, int(current))
    except Exception as exc:
        logger.warning("Could not refresh the storage usage cache: %s", exc)
    return usage


async def _check_storage_limit(
    size_bytes: int, db: AsyncSession, config: dict[str, Any] | None = None
) -> None:
    """Raise if the global storage limit (max_storage_gb) would be exceeded."""
    max_gb = (
        config.get("max_storage_gb")
        if config and config.get("max_storage_gb") is not None
        else settings.max_storage_gb
    )
    if not max_gb:
        return

    max_bytes = max_gb * 1024 * 1024 * 1024
    redis = redis_core.redis_client

    usage = await _get_storage_usage(db, redis)

    if usage + size_bytes > max_bytes:
        logger.warning(
            "Storage limit reached: %d bytes usage + %d bytes upload > %d bytes limit",
            usage,
            size_bytes,
            max_bytes,
        )
        raise BadRequestError(
            f"Global storage limit reached ({max_gb} GB). Please contact an administrator.",
            code=UploadErrorCode.STORAGE_FULL,
        )


async def _reserve_storage_limit(
    size_bytes: int,
    reservation_id: str,
    redis: "Redis",  # type: ignore[type-arg]
    db: AsyncSession,
) -> None:
    """Atomically reserve global capacity for an in-flight upload."""
    if not settings.max_storage_gb:
        return

    # Ensure the physical-usage key exists. The reservation Lua script reads it
    # atomically with reservation totals, so this value must not be passed as a
    # stale argument from Python.
    await _get_storage_usage(db, redis)
    max_bytes = int(settings.max_storage_gb * 1024 * 1024 * 1024)
    now = int(time.time())
    try:
        reserve = redis.register_script(_STORAGE_RESERVE_SCRIPT)
        accepted = await reserve(
            keys=[
                _STORAGE_RESERVATION_EXPIRIES,
                _STORAGE_RESERVATION_SIZES,
                _STORAGE_RESERVATION_TOTAL,
                _STORAGE_USAGE_KEY,
            ],
            args=[
                reservation_id,
                size_bytes,
                now + _STORAGE_RESERVATION_TTL,
                now,
                max_bytes,
            ],
            client=redis,
        )
    except Exception as exc:
        logger.error("Cannot enforce the global storage reservation: %s", exc)
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc

    if int(accepted) != 1:
        raise BadRequestError(
            f"Global storage limit reached ({settings.max_storage_gb} GB). "
            "Please contact an administrator.",
            code=UploadErrorCode.STORAGE_FULL,
        )


async def _release_storage_reservation(
    reservation_id: str, redis: Any
) -> None:
    """Release a capacity reservation; repeated calls are harmless."""
    if not settings.max_storage_gb:
        return
    release = redis.register_script(_STORAGE_RELEASE_SCRIPT)
    await release(
        keys=[
            _STORAGE_RESERVATION_EXPIRIES,
            _STORAGE_RESERVATION_SIZES,
            _STORAGE_RESERVATION_TOTAL,
        ],
        args=[reservation_id],
        client=redis,
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
            from app.core.database.database import async_session_factory

            async with async_session_factory() as fallback_db:
                db_count = (
                    await fallback_db.scalar(
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
