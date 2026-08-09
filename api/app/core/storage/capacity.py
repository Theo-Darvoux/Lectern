"""Global object-storage capacity accounting and reservation operations."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.redis as redis_core
from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.core.common.upload_errors import UploadErrorCode
from app.core.security.cas import _STORAGE_USAGE_GENERATION_KEY, _STORAGE_USAGE_KEY
from app.core.storage.facade import list_objects
from app.models.material import MaterialVersion

logger = logging.getLogger(__name__)

_LUA_DIR = Path(__file__).parents[1] / "database" / "lua"
_STORAGE_RESERVE_SCRIPT = (_LUA_DIR / "storage_reserve.lua").read_text(encoding="utf-8")
_STORAGE_RELEASE_SCRIPT = (_LUA_DIR / "storage_release.lua").read_text(encoding="utf-8")
_STORAGE_RELEASE_PROMOTED_LEGACY_SCRIPT = (
    _LUA_DIR / "storage_release_promoted_legacy.lua"
).read_text(encoding="utf-8")
_STORAGE_RECONCILE_CAS_SCRIPT = (_LUA_DIR / "storage_reconcile_cas_usage.lua").read_text(
    encoding="utf-8"
)
_STORAGE_COMMIT_CAS_DELTA_SCRIPT = (_LUA_DIR / "storage_commit_cas_delta.lua").read_text(
    encoding="utf-8"
)
_STORAGE_RESERVATION_EXPIRIES = "storage:upload_reservations:expiries"
_STORAGE_RESERVATION_SIZES = "storage:upload_reservations:sizes"
_STORAGE_RESERVATION_TOTAL = "storage:upload_reservations:total"
_STORAGE_RESERVATION_TTL = 3 * 3600
_STORAGE_SNAPSHOT_MAX_RETRIES = 8
_STORAGE_USAGE_DIRTY_KEY = "storage:total_usage_dirty"
_CAS_STORAGE_MUTATION_LOCK = "storage:cas-physical-usage"
_CAS_STORAGE_LOCK_TIMEOUT = 120.0
_CAS_STORAGE_LOCK_EXPIRE = 300.0

LEGACY_STORAGE_USAGE_KEY = "storage:legacy_usage_bytes"
LEGACY_STORAGE_GENERATION_KEY = "storage:legacy_usage_generation"


async def _physical_cas_storage_usage() -> int:
    """Return exact physical bytes currently present under the cas/ prefix."""
    total = 0
    async for obj in list_objects(prefix="cas/"):
        try:
            size = int(obj.get("Size", 0))
        except (TypeError, ValueError) as exc:
            raise BadRequestError("Storage capacity accounting state is invalid") from exc
        if size < 0:
            raise BadRequestError("Storage capacity accounting state is invalid")
        total += size
    return total


async def _cas_storage_generation(redis: Any) -> int:
    try:
        raw = await redis.get(_STORAGE_USAGE_GENERATION_KEY)
        return int(raw or 0)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("Storage capacity accounting state is invalid") from exc
    except Exception as exc:
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc


async def _reconcile_cas_storage_usage_locked(redis: Any) -> int:
    """Rebuild physical CAS usage while the mutation lock is held."""
    for _attempt in range(_STORAGE_SNAPSHOT_MAX_RETRIES):
        generation = await _cas_storage_generation(redis)
        try:
            physical_usage = await _physical_cas_storage_usage()
            reconcile = redis.register_script(_STORAGE_RECONCILE_CAS_SCRIPT)
            result = int(
                await reconcile(
                    keys=[
                        _STORAGE_USAGE_KEY,
                        _STORAGE_USAGE_GENERATION_KEY,
                        _STORAGE_USAGE_DIRTY_KEY,
                    ],
                    args=[generation, physical_usage],
                    client=redis,
                )
            )
        except BadRequestError:
            raise
        except Exception as exc:
            logger.error("Cannot reconcile physical CAS storage usage: %s", exc)
            raise BadRequestError(
                "Storage capacity is temporarily unavailable. Please try again later."
            ) from exc
        if result == 1:
            return physical_usage
        if result == 0:
            continue
        raise BadRequestError("Storage capacity accounting state is invalid")
    raise BadRequestError(
        "Storage capacity changed repeatedly while reconciling physical usage. Please retry."
    )


async def _read_cached_cas_storage_usage(redis: Any) -> int | None:
    """Read one atomic CAS accounting snapshot without taking the mutation lock."""
    try:
        state = await redis.mget(
            _STORAGE_USAGE_KEY,
            _STORAGE_USAGE_GENERATION_KEY,
            _STORAGE_USAGE_DIRTY_KEY,
        )
    except Exception as exc:
        logger.error("Storage usage cache unavailable: %s", exc)
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc

    if not isinstance(state, (list, tuple)) or len(state) != 3:
        raise BadRequestError("Storage capacity accounting state is invalid")

    usage_raw, generation_raw, dirty_raw = state
    if usage_raw is None or generation_raw is None or dirty_raw is not None:
        return None

    try:
        usage = int(usage_raw)
        generation = int(generation_raw)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("Storage capacity accounting state is invalid") from exc
    if usage < 0 or generation < 0:
        raise BadRequestError("Storage capacity accounting state is invalid")
    return usage


async def _cached_cas_storage_usage_locked(redis: Any) -> int:
    """Return a valid cache value, rebuilding it when incomplete or dirty."""
    usage = await _read_cached_cas_storage_usage(redis)
    if usage is not None:
        return usage
    return await _reconcile_cas_storage_usage_locked(redis)


@asynccontextmanager
async def cas_storage_mutation(redis: Any) -> AsyncIterator[None]:
    """Serialize physical ``cas/`` mutations with cache reconciliation."""
    async with redis_core.redis_lock(
        redis,
        _CAS_STORAGE_MUTATION_LOCK,
        timeout=_CAS_STORAGE_LOCK_TIMEOUT,
        expire=_CAS_STORAGE_LOCK_EXPIRE,
    ):
        await _cached_cas_storage_usage_locked(redis)
        yield


async def mark_cas_storage_usage_dirty(redis: Any) -> None:
    """Mark the aggregate cache unusable before physical CAS I/O begins."""
    await redis.set(_STORAGE_USAGE_DIRTY_KEY, "1")


async def commit_cas_storage_delta(redis: Any, delta_bytes: int) -> int:
    """Publish one completed physical CAS size delta under the mutation lock."""
    commit = redis.register_script(_STORAGE_COMMIT_CAS_DELTA_SCRIPT)
    try:
        result = int(
            await commit(
                keys=[
                    _STORAGE_USAGE_KEY,
                    _STORAGE_USAGE_GENERATION_KEY,
                    _STORAGE_USAGE_DIRTY_KEY,
                ],
                args=[delta_bytes],
                client=redis,
            )
        )
    except Exception as exc:
        logger.error("Cannot commit physical CAS storage delta: %s", exc)
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc

    if result >= 0:
        return result
    if result in {-1, -3}:
        # Cache eviction or an impossible negative delta after I/O: reconstruct
        # from object storage while the same mutation lock is still held.
        return await _reconcile_cas_storage_usage_locked(redis)
    raise BadRequestError("Storage capacity accounting state is invalid")


async def reconcile_cas_storage_usage(redis: Any) -> int:
    """Serialize and generation-fence an exact object-store usage scan."""
    async with redis_core.redis_lock(
        redis,
        _CAS_STORAGE_MUTATION_LOCK,
        timeout=_CAS_STORAGE_LOCK_TIMEOUT,
        expire=_CAS_STORAGE_LOCK_EXPIRE,
    ):
        return await _reconcile_cas_storage_usage_locked(redis)


async def get_storage_usage(db: AsyncSession, redis: Redis) -> int:  # type: ignore[type-arg]
    """Return exact physical CAS usage, rebuilding an incomplete/dirty cache."""
    del db  # Physical capacity is defined by object-store bytes, not logical DB ownership.

    # A complete clean cache can be consumed without the mutation lock. MGET is
    # one atomic Redis snapshot, so usage/generation/dirty cannot be mixed across
    # a concurrent physical-delta commit. Missing or dirty state still enters the
    # serialized reconciliation path.
    usage = await _read_cached_cas_storage_usage(redis)
    if usage is not None:
        return usage

    async with redis_core.redis_lock(
        redis,
        _CAS_STORAGE_MUTATION_LOCK,
        timeout=_CAS_STORAGE_LOCK_TIMEOUT,
        expire=_CAS_STORAGE_LOCK_EXPIRE,
    ):
        return await _cached_cas_storage_usage_locked(redis)


async def _legacy_storage_usage_from_database(db: AsyncSession) -> int:
    """Return physical legacy usage until cleanup confirms object deletion.

    Expired soft-deleted MaterialVersion rows intentionally remain authoritative
    capacity owners until the cleanup worker deletes the backing object and then
    hard-deletes those rows. This makes deletion failure fail closed (overcount),
    rather than admitting new uploads while bytes still exist in object storage.
    """
    legacy_objects = (
        select(
            MaterialVersion.file_key,
            func.max(MaterialVersion.file_size).label("size"),
        )
        .where(
            MaterialVersion.file_key.is_not(None),
            MaterialVersion.file_key.not_like("cas/%"),
            MaterialVersion.file_size.is_not(None),
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
