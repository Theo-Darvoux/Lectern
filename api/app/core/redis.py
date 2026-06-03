from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import BusyLoadingError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import settings

logger = logging.getLogger(__name__)

# Retry policy for transient Redis errors (BGSAVE latency spikes, brief network
# blips). Six attempts with exponential backoff capped at 10s covers ~60s of
# sustained unavailability before giving up.
_REDIS_RETRY = Retry(ExponentialBackoff(cap=10, base=1.0), retries=6)
_REDIS_RETRY_ERRORS: list[type[Exception]] = [
    RedisConnectionError,
    RedisTimeoutError,
    BusyLoadingError,
]


def build_redis_settings() -> RedisSettings:
    """Return arq RedisSettings with resilient connection and retry config."""
    base = RedisSettings.from_dsn(settings.redis_url)
    return dataclasses.replace(
        base,
        # 10s socket-connect timeout (default is 1s — too tight for BGSAVE spikes)
        conn_timeout=10,
        # Retry pool creation up to 10 times on startup (e.g. Redis not ready yet)
        conn_retries=10,
        conn_retry_delay=2,
        # Retry individual commands that time out instead of propagating the error
        retry_on_timeout=True,
        retry_on_error=_REDIS_RETRY_ERRORS,
        retry=_REDIS_RETRY,
    )


redis_client: Redis = Redis.from_url(  # type: ignore[type-arg, call-overload]
    settings.redis_url,
    decode_responses=True,
    retry_on_timeout=True,
    retry_on_error=_REDIS_RETRY_ERRORS,
    retry=_REDIS_RETRY,
)
arq_pool: ArqRedis | None = None


async def get_redis() -> AsyncGenerator[Redis, None]:  # type: ignore[type-arg]
    yield redis_client


async def init_arq_pool() -> None:
    global arq_pool
    arq_pool = await create_pool(build_redis_settings())


async def close_arq_pool() -> None:
    if arq_pool:
        await arq_pool.close()


@asynccontextmanager
async def redis_lock(
    redis: Redis,  # type: ignore[type-arg]
    lock_name: str,
    timeout: float = 10.0,
    retry_interval: float = 0.1,
    expire: int = 30,
) -> AsyncGenerator[None, None]:
    """Simple distributed lock using SET NX.

    Args:
        redis: Redis client instance.
        lock_name: Unique name for the lock.
        timeout: Max seconds to wait for the lock.
        retry_interval: Seconds between acquisition attempts.
        expire: Lock TTL in seconds (auto-release if process dies).
    """
    lock_key = f"lock:{lock_name}"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while True:
        # SET with NX and EX (expire) is atomic in Redis 2.6.12+
        if await redis.set(lock_key, "1", ex=expire, nx=True):
            try:
                yield
                return
            finally:
                await redis.delete(lock_key)

        if loop.time() >= deadline:
            raise TimeoutError(f"Could not acquire lock {lock_name} within {timeout}s")

        await asyncio.sleep(retry_interval)


@asynccontextmanager
async def redis_semaphore(
    redis: Redis,  # type: ignore[type-arg]
    sem_name: str,
    limit: int,
    timeout: float = 60.0,
    retry_interval: float = 0.2,
    expire: int = 300,
) -> AsyncGenerator[None, None]:
    """Distributed semaphore using Redis.

    Args:
        redis: Redis client instance.
        sem_name: Unique name for the semaphore.
        limit: Max concurrent holders.
        timeout: Max seconds to wait for a slot.
        retry_interval: Seconds between acquisition attempts.
        expire: Key TTL in seconds (auto-release if process dies).
    """
    sem_key = f"sem:{sem_name}"
    loop = asyncio.get_running_loop()
    holder_id = f"{settings.environment}:{loop.time()}"
    deadline = loop.time() + timeout

    # Lua script for atomic semaphore acquisition
    # ARGV: [1] limit, [2] expire (ms), [3] holder_id
    acquire_script = """
    local sem_key = KEYS[1]
    local limit = tonumber(ARGV[1])
    local expire_ms = tonumber(ARGV[2])
    local holder_id = ARGV[3]

    -- Cleanup expired holders (using a ZSET for TTLs)
    redis.call('ZREMRANGEBYSCORE', sem_key, 0, ARGV[4])

    local count = redis.call('ZCARD', sem_key)
    if count < limit then
        redis.call('ZADD', sem_key, ARGV[5], holder_id)
        return 1
    end
    return 0
    """

    while True:
        now_ms = int(loop.time() * 1000)
        expires_at = now_ms + (expire * 1000)

        # Run Lua script: keys=[sem_key], args=[limit, expire_ms, holder_id, now_ms, expires_at]
        res = await redis.eval(  # type: ignore[no-untyped-call, misc]
            acquire_script,
            1,
            sem_key,
            str(limit),
            str(expire * 1000),
            holder_id,
            str(now_ms),
            str(expires_at),
        )

        if res == 1:
            try:
                yield
                return
            finally:
                await redis.zrem(sem_key, holder_id)

        if loop.time() >= deadline:
            raise TimeoutError(f"Could not acquire semaphore {sem_name} within {timeout}s")

        await asyncio.sleep(retry_interval)
