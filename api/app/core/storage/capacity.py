"""Global object-storage capacity accounting and reservation operations."""

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.redis as redis_core
from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.core.common.upload_errors import UploadErrorCode
from app.core.security.cas import _STORAGE_USAGE_KEY
from app.models.material import MaterialVersion
from app.models.upload import Upload

logger = logging.getLogger(__name__)

_LUA_DIR = Path(__file__).parents[1] / "database" / "lua"
_STORAGE_RESERVE_SCRIPT = (_LUA_DIR / "storage_reserve.lua").read_text(encoding="utf-8")
_STORAGE_RELEASE_SCRIPT = (_LUA_DIR / "storage_release.lua").read_text(encoding="utf-8")
_STORAGE_RELEASE_PROMOTED_LEGACY_SCRIPT = (
    _LUA_DIR / "storage_release_promoted_legacy.lua"
).read_text(encoding="utf-8")
_STORAGE_RESERVATION_EXPIRIES = "storage:upload_reservations:expiries"
_STORAGE_RESERVATION_SIZES = "storage:upload_reservations:sizes"
_STORAGE_RESERVATION_TOTAL = "storage:upload_reservations:total"
_STORAGE_RESERVATION_TTL = 3 * 3600
_STORAGE_SNAPSHOT_MAX_RETRIES = 8

LEGACY_STORAGE_USAGE_KEY = "storage:legacy_usage_bytes"
LEGACY_STORAGE_GENERATION_KEY = "storage:legacy_usage_generation"


async def get_storage_usage(db: AsyncSession, redis: Redis) -> int:  # type: ignore[type-arg]
    """Return physical CAS usage, rebuilding the cache from the database if needed."""
    revert_cutoff = datetime.now(UTC) - timedelta(days=settings.pr_revert_grace_days)
    material_refs = select(
        MaterialVersion.cas_sha256.label("sha256"),
        MaterialVersion.file_size.label("size"),
    ).where(
        MaterialVersion.cas_sha256.is_not(None),
        MaterialVersion.file_key.like("cas/%"),
        (MaterialVersion.deleted_at.is_(None)) | (MaterialVersion.deleted_at >= revert_cutoff),
    )
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
        select(func.max(all_refs.c.size).label("size")).group_by(all_refs.c.sha256).subquery()
    )

    async def from_database() -> int:
        stmt = select(func.sum(unique_sizes.c.size)).execution_options(include_deleted=True)
        return int(await db.scalar(stmt) or 0)

    try:
        usage_raw = await redis.get(_STORAGE_USAGE_KEY)
    except Exception as exc:
        logger.warning("Storage usage cache unavailable; using the database: %s", exc)
        return await from_database()
    if usage_raw is not None:
        return max(0, int(usage_raw))

    usage = await from_database()
    try:
        # Do not overwrite a CAS increment that raced the database rebuild.
        initialized = await redis.set(_STORAGE_USAGE_KEY, usage, nx=True)
        if not initialized:
            current = await redis.get(_STORAGE_USAGE_KEY)
            if current is not None:
                return max(0, int(current))
    except Exception as exc:
        logger.warning("Could not refresh the storage usage cache: %s", exc)
    return usage


async def _legacy_storage_usage_from_database(db: AsyncSession) -> int:
    """Return retained legacy usage, including soft-deleted rows in revert grace."""
    revert_cutoff = datetime.now(UTC) - timedelta(days=settings.pr_revert_grace_days)
    legacy_objects = (
        select(
            MaterialVersion.file_key,
            func.max(MaterialVersion.file_size).label("size"),
        )
        .where(
            MaterialVersion.file_key.is_not(None),
            MaterialVersion.file_key.not_like("cas/%"),
            MaterialVersion.file_size.is_not(None),
            (MaterialVersion.deleted_at.is_(None)) | (MaterialVersion.deleted_at >= revert_cutoff),
        )
        .group_by(MaterialVersion.file_key)
        .subquery()
    )
    stmt = select(func.sum(legacy_objects.c.size)).execution_options(include_deleted=True)
    return int(await db.scalar(stmt) or 0)


async def refresh_legacy_storage_usage(
    db: AsyncSession,
    redis: Redis,  # type: ignore[type-arg]
) -> int:
    """Recompute retained legacy usage and overwrite its Redis cache."""
    usage = await _legacy_storage_usage_from_database(db)
    try:
        await redis.set(LEGACY_STORAGE_USAGE_KEY, usage)
    except Exception as exc:
        logger.error("Could not refresh the legacy storage usage cache: %s", exc)
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc
    return usage


async def _legacy_storage_generation(redis: Any) -> int:
    try:
        raw = await redis.get(LEGACY_STORAGE_GENERATION_KEY)
        return int(raw or 0)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("Storage capacity accounting state is invalid") from exc
    except Exception as exc:
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc


async def check_storage_limit(
    size_bytes: int,
    db: AsyncSession,
    config: dict[str, Any] | None = None,
) -> None:
    """Raise if the configured global storage limit would be exceeded."""
    max_gb = (
        config.get("max_storage_gb")
        if config and config.get("max_storage_gb") is not None
        else settings.max_storage_gb
    )
    if not max_gb:
        return

    max_bytes = max_gb * 1024 * 1024 * 1024
    redis = redis_core.redis_client
    usage = await get_storage_usage(db, redis) + await _legacy_storage_usage_from_database(db)
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


async def reserve_storage_limit(
    size_bytes: int,
    reservation_id: str,
    redis: Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    *,
    ttl_seconds: int = _STORAGE_RESERVATION_TTL,
) -> None:
    """Atomically reserve global capacity for an in-flight upload."""
    if ttl_seconds <= 0:
        raise ValueError("reservation TTL must be positive")
    if not settings.max_storage_gb:
        return

    await get_storage_usage(db, redis)
    max_bytes = int(settings.max_storage_gb * 1024 * 1024 * 1024)

    for _attempt in range(_STORAGE_SNAPSHOT_MAX_RETRIES):
        generation = await _legacy_storage_generation(redis)
        legacy_usage = await _legacy_storage_usage_from_database(db)
        now = int(time.time())
        try:
            reserve = redis.register_script(_STORAGE_RESERVE_SCRIPT)
            accepted = await reserve(
                keys=[
                    _STORAGE_RESERVATION_EXPIRIES,
                    _STORAGE_RESERVATION_SIZES,
                    _STORAGE_RESERVATION_TOTAL,
                    _STORAGE_USAGE_KEY,
                    LEGACY_STORAGE_USAGE_KEY,
                    LEGACY_STORAGE_GENERATION_KEY,
                ],
                args=[
                    reservation_id,
                    size_bytes,
                    now + ttl_seconds,
                    now,
                    max_bytes,
                    generation,
                    legacy_usage,
                ],
                client=redis,
            )
        except Exception as exc:
            logger.error("Cannot enforce the global storage reservation: %s", exc)
            raise BadRequestError(
                "Storage capacity is temporarily unavailable. Please try again later."
            ) from exc

        result = int(accepted)
        if result == -2:
            continue
        if result == 1:
            return
        if result == 0:
            raise BadRequestError(
                f"Global storage limit reached ({settings.max_storage_gb} GB). "
                "Please contact an administrator.",
                code=UploadErrorCode.STORAGE_FULL,
            )
        raise BadRequestError("Storage capacity accounting state is invalid")

    raise BadRequestError(
        "Storage capacity changed repeatedly while reserving space. Please retry."
    )


async def release_storage_reservation(reservation_id: str, redis: Any) -> None:
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


async def release_promoted_legacy_storage_reservation(reservation_id: str, redis: Any) -> None:
    """Release a promoted legacy reservation and fence stale DB snapshots atomically."""
    if not settings.max_storage_gb:
        return
    release = redis.register_script(_STORAGE_RELEASE_PROMOTED_LEGACY_SCRIPT)
    result = await release(
        keys=[
            _STORAGE_RESERVATION_EXPIRIES,
            _STORAGE_RESERVATION_SIZES,
            _STORAGE_RESERVATION_TOTAL,
            LEGACY_STORAGE_GENERATION_KEY,
            LEGACY_STORAGE_USAGE_KEY,
        ],
        args=[reservation_id],
        client=redis,
    )
    if int(result) < 0:
        raise RuntimeError("invalid promoted-legacy reservation state")
