"""Global object-storage capacity accounting and reservation operations."""

import asyncio
import json
import logging
import time
import uuid
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
_STORAGE_BEGIN_CAS_MUTATION_SCRIPT = (_LUA_DIR / "storage_begin_cas_mutation.lua").read_text(
    encoding="utf-8"
)
_STORAGE_ABORT_CAS_MUTATION_SCRIPT = (_LUA_DIR / "storage_abort_cas_mutation.lua").read_text(
    encoding="utf-8"
)
_STORAGE_RESOLVE_CAS_MUTATION_SCRIPT = (_LUA_DIR / "storage_resolve_cas_mutation.lua").read_text(
    encoding="utf-8"
)
_STORAGE_RESERVATION_EXPIRIES = "storage:upload_reservations:expiries"
_STORAGE_RESERVATION_SIZES = "storage:upload_reservations:sizes"
_STORAGE_RESERVATION_TOTAL = "storage:upload_reservations:total"
_STORAGE_RESERVATION_TTL = 3 * 3600
_STORAGE_SNAPSHOT_MAX_RETRIES = 8
_STORAGE_USAGE_DIRTY_KEY = "storage:total_usage_dirty"
_STORAGE_MUTATION_EPOCH_KEY = "storage:cas_mutation_epoch"
_STORAGE_MUTATION_INTENTS_KEY = "storage:cas_mutation_intents"
_CAS_STORAGE_MUTATION_LOCK = "storage:cas-physical-usage"
_CAS_MUTATION_DURABILITY_TIMEOUT_MS = 5_000
_CAS_MUTATION_RECOVERY_STABILITY_SECONDS = 2.0
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


async def _cas_mutation_epoch(redis: Any) -> int:
    try:
        raw = await redis.get(_STORAGE_MUTATION_EPOCH_KEY)
        epoch = int(raw or 0)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("Storage capacity accounting state is invalid") from exc
    except Exception as exc:
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc
    if epoch < 0:
        raise BadRequestError("Storage capacity accounting state is invalid")
    return epoch


async def _pending_cas_mutation_count(redis: Any) -> int:
    try:
        count = int(await redis.hlen(_STORAGE_MUTATION_INTENTS_KEY))
    except (TypeError, ValueError) as exc:
        raise BadRequestError("Storage capacity accounting state is invalid") from exc
    except Exception as exc:
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc
    if count < 0:
        raise BadRequestError("Storage capacity accounting state is invalid")
    return count


async def _wait_for_cas_mutation_durability(redis: Any) -> None:
    try:
        result = await redis.execute_command("WAITAOF", 1, 0, _CAS_MUTATION_DURABILITY_TIMEOUT_MS)
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise ValueError("unexpected WAITAOF response")
        local_fsyncs = int(result[0])
    except Exception as exc:
        raise BadRequestError(
            "Storage capacity journal is temporarily unavailable. Please try again later."
        ) from exc
    if local_fsyncs < 1:
        raise BadRequestError(
            "Storage capacity journal could not be persisted. Please try again later."
        )


async def abort_cas_storage_mutation(
    redis: Any,
    mutation_id: str,
    mutation_epoch: int,
) -> None:
    abort = redis.register_script(_STORAGE_ABORT_CAS_MUTATION_SCRIPT)
    result = int(
        await abort(
            keys=[
                _STORAGE_USAGE_DIRTY_KEY,
                _STORAGE_MUTATION_EPOCH_KEY,
                _STORAGE_MUTATION_INTENTS_KEY,
            ],
            args=[mutation_id, mutation_epoch],
            client=redis,
        )
    )
    if result != 1:
        raise BadRequestError("Storage capacity mutation journal changed unexpectedly")


async def _redis_time_ms(redis: Any) -> int:
    try:
        raw = await redis.time()
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError("unexpected Redis TIME response")
        seconds, microseconds = int(raw[0]), int(raw[1])
    except (TypeError, ValueError) as exc:
        raise BadRequestError("Storage capacity accounting state is invalid") from exc
    except Exception as exc:
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc
    if seconds < 0 or microseconds < 0:
        raise BadRequestError("Storage capacity accounting state is invalid")
    return seconds * 1000 + microseconds // 1000


def _decode_cas_mutation_intent(raw: Any) -> tuple[str, str, int, int, int]:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode()
        payload = raw if isinstance(raw, dict) else json.loads(raw)
        operation = str(payload["operation"])
        file_key = str(payload["file_key"])
        started_at_ms = int(payload["started_at_ms"])
        epoch = int(payload["epoch"])
        raw_recover_after = payload.get("recover_after_ms")
        if raw_recover_after is None:
            # Upgrade compatibility for a v1 intent already present during rollout.
            raw_recover_after = (
                started_at_ms
                + (
                    settings.cas_mutation_io_timeout_seconds
                    + settings.cas_mutation_recovery_grace_seconds
                )
                * 1000
            )
        recover_after_ms = int(raw_recover_after)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BadRequestError("Storage capacity mutation journal is invalid") from exc

    if operation not in {"write", "delete", "move"}:
        raise BadRequestError("Storage capacity mutation journal is invalid")
    if not file_key.startswith("cas/"):
        raise BadRequestError("Storage capacity mutation journal is invalid")
    if started_at_ms <= 0 or epoch <= 0 or recover_after_ms <= started_at_ms:
        raise BadRequestError("Storage capacity mutation journal is invalid")
    return operation, file_key, started_at_ms, recover_after_ms, epoch


async def _read_pending_cas_mutation(
    redis: Any,
) -> tuple[str, str, str, int, int, int] | None:
    try:
        intents = await redis.hgetall(_STORAGE_MUTATION_INTENTS_KEY)
    except Exception as exc:
        raise BadRequestError(
            "Storage capacity is temporarily unavailable. Please try again later."
        ) from exc
    if not isinstance(intents, dict):
        raise BadRequestError("Storage capacity mutation journal is invalid")
    if not intents:
        return None
    if len(intents) != 1:
        raise BadRequestError("Storage capacity mutation journal is invalid")

    mutation_id_raw, raw_intent = next(iter(intents.items()))
    mutation_id = (
        mutation_id_raw.decode() if isinstance(mutation_id_raw, bytes) else str(mutation_id_raw)
    )
    operation, file_key, started_at_ms, recover_after_ms, epoch = _decode_cas_mutation_intent(
        raw_intent
    )
    if await _cas_mutation_epoch(redis) != epoch:
        raise BadRequestError("Storage capacity mutation journal is invalid")
    return mutation_id, operation, file_key, started_at_ms, recover_after_ms, epoch


async def _resolve_cas_storage_mutation(
    redis: Any,
    mutation_id: str,
    mutation_epoch: int,
    physical_usage: int,
) -> int:
    resolve = redis.register_script(_STORAGE_RESOLVE_CAS_MUTATION_SCRIPT)
    result = int(
        await resolve(
            keys=[
                _STORAGE_USAGE_KEY,
                _STORAGE_USAGE_GENERATION_KEY,
                _STORAGE_USAGE_DIRTY_KEY,
                _STORAGE_MUTATION_EPOCH_KEY,
                _STORAGE_MUTATION_INTENTS_KEY,
            ],
            args=[mutation_id, mutation_epoch, physical_usage],
            client=redis,
        )
    )
    if result >= 0:
        return result
    if result in {-1, 0}:
        raise BadRequestError("Storage capacity mutation journal changed unexpectedly")
    raise BadRequestError("Storage capacity accounting state is invalid")


async def resolve_cas_storage_mutation_by_scan(
    redis: Any,
    mutation_id: str,
    mutation_epoch: int,
) -> int:
    physical_usage = await _physical_cas_storage_usage()
    return await _resolve_cas_storage_mutation(redis, mutation_id, mutation_epoch, physical_usage)


async def _recover_stale_cas_mutation_locked(redis: Any) -> bool:
    """Recover one abandoned physical mutation after its remote ambiguity window.

    The caller owns the global physical-CAS lock. New journal entries record a
    Redis-server-time recovery deadline equal to the live I/O hard timeout plus
    an extra grace period. We never reap a fresh intent. Once stale, two exact
    scans separated by a stability probe must agree before the exact intent/epoch
    may be resolved. This keeps recovery fail-closed while giving dead owners an
    autonomous convergence path.
    """
    pending = await _read_pending_cas_mutation(redis)
    if pending is None:
        return False
    mutation_id, _operation, _file_key, _started_at_ms, recover_after_ms, epoch = pending
    if await _redis_time_ms(redis) < recover_after_ms:
        return False

    first_usage = await _physical_cas_storage_usage()
    if _CAS_MUTATION_RECOVERY_STABILITY_SECONDS > 0:
        await asyncio.sleep(_CAS_MUTATION_RECOVERY_STABILITY_SECONDS)

    pending_after_probe = await _read_pending_cas_mutation(redis)
    if pending_after_probe is None:
        # The original owner completed while the stability probe was running.
        return True
    if pending_after_probe[0] != mutation_id or pending_after_probe[5] != epoch:
        raise BadRequestError("Storage capacity mutation journal changed unexpectedly")

    second_usage = await _physical_cas_storage_usage()
    if second_usage != first_usage:
        raise BadRequestError(
            "Physical CAS storage is still changing while recovering an abandoned mutation. "
            "Please retry."
        )

    await _resolve_cas_storage_mutation(redis, mutation_id, epoch, second_usage)
    logger.warning(
        "Recovered abandoned CAS mutation %s at exact physical usage %d bytes",
        mutation_id,
        second_usage,
    )
    return True


async def _reconcile_cas_storage_usage_locked(redis: Any) -> int:
    if await _pending_cas_mutation_count(redis):
        await _recover_stale_cas_mutation_locked(redis)
        if await _pending_cas_mutation_count(redis):
            raise BadRequestError(
                "Storage capacity is temporarily unavailable while a physical CAS mutation resolves."
            )

    for _attempt in range(_STORAGE_SNAPSHOT_MAX_RETRIES):
        generation = await _cas_storage_generation(redis)
        mutation_epoch = await _cas_mutation_epoch(redis)
        try:
            physical_usage = await _physical_cas_storage_usage()
            reconcile = redis.register_script(_STORAGE_RECONCILE_CAS_SCRIPT)
            result = int(
                await reconcile(
                    keys=[
                        _STORAGE_USAGE_KEY,
                        _STORAGE_USAGE_GENERATION_KEY,
                        _STORAGE_USAGE_DIRTY_KEY,
                        _STORAGE_MUTATION_EPOCH_KEY,
                        _STORAGE_MUTATION_INTENTS_KEY,
                    ],
                    args=[generation, mutation_epoch, physical_usage],
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
        if result == -3:
            raise BadRequestError(
                "Storage capacity is temporarily unavailable while a physical CAS mutation resolves."
            )
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
    if await _pending_cas_mutation_count(redis):
        await _recover_stale_cas_mutation_locked(redis)
        if await _pending_cas_mutation_count(redis):
            raise BadRequestError(
                "Storage capacity is temporarily unavailable while a physical CAS mutation resolves."
            )
    usage = await _read_cached_cas_storage_usage(redis)
    if usage is not None:
        return usage
    return await _reconcile_cas_storage_usage_locked(redis)


@asynccontextmanager
async def cas_storage_mutation(
    redis: Any,
    file_key: str,
    operation: str,
) -> AsyncIterator[tuple[str, int]]:
    async with redis_core.redis_lock(
        redis,
        _CAS_STORAGE_MUTATION_LOCK,
        timeout=_CAS_STORAGE_LOCK_TIMEOUT,
        expire=_CAS_STORAGE_LOCK_EXPIRE,
    ):
        await _cached_cas_storage_usage_locked(redis)
        mutation_id = uuid.uuid4().hex
        # WAITAOF only covers writes previously issued on the *same Redis
        # connection*. Pin the begin script and WAITAOF to one redis-py
        # single-connection client; two pooled commands would not suffice.
        async with redis.client() as journal_redis:
            begin = journal_redis.register_script(_STORAGE_BEGIN_CAS_MUTATION_SCRIPT)
            result = int(
                await begin(
                    keys=[
                        _STORAGE_USAGE_DIRTY_KEY,
                        _STORAGE_MUTATION_EPOCH_KEY,
                        _STORAGE_MUTATION_INTENTS_KEY,
                    ],
                    args=[
                        mutation_id,
                        operation,
                        file_key,
                        (
                            settings.cas_mutation_io_timeout_seconds
                            + settings.cas_mutation_recovery_grace_seconds
                        )
                        * 1000,
                    ],
                    client=journal_redis,
                )
            )
            if result == -1:
                raise BadRequestError(
                    "Storage capacity is temporarily unavailable while a physical CAS mutation resolves."
                )
            if result < 0:
                raise BadRequestError("Storage capacity accounting state is invalid")
            try:
                await _wait_for_cas_mutation_durability(journal_redis)
            except BaseException:
                try:
                    await abort_cas_storage_mutation(redis, mutation_id, result)
                except Exception:
                    logger.exception("Could not roll back an unpersisted CAS mutation intent")
                raise
        yield mutation_id, result


async def mark_cas_storage_usage_dirty(redis: Any) -> None:
    """Mark the aggregate cache unusable before physical CAS I/O begins."""
    await redis.set(_STORAGE_USAGE_DIRTY_KEY, "1")


async def commit_cas_storage_delta(
    redis: Any,
    delta_bytes: int,
    mutation_id: str,
    mutation_epoch: int,
) -> int:
    commit = redis.register_script(_STORAGE_COMMIT_CAS_DELTA_SCRIPT)
    try:
        result = int(
            await commit(
                keys=[
                    _STORAGE_USAGE_KEY,
                    _STORAGE_USAGE_GENERATION_KEY,
                    _STORAGE_USAGE_DIRTY_KEY,
                    _STORAGE_MUTATION_EPOCH_KEY,
                    _STORAGE_MUTATION_INTENTS_KEY,
                ],
                args=[delta_bytes, mutation_id, mutation_epoch],
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
        return await resolve_cas_storage_mutation_by_scan(redis, mutation_id, mutation_epoch)
    if result == -4:
        raise BadRequestError("Storage capacity mutation journal changed unexpectedly")
    raise BadRequestError("Storage capacity accounting state is invalid")


async def recover_stale_cas_storage_mutation(redis: Any) -> bool:
    """Autonomously recover an orphaned durable mutation once it is safe."""
    async with redis_core.redis_lock(
        redis,
        _CAS_STORAGE_MUTATION_LOCK,
        timeout=_CAS_STORAGE_LOCK_TIMEOUT,
        expire=_CAS_STORAGE_LOCK_EXPIRE,
    ):
        return await _recover_stale_cas_mutation_locked(redis)


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
    del db
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
