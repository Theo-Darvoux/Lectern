from __future__ import annotations

import asyncio
import json
import os
from time import monotonic
from typing import Any

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

import app.core.database.redis as redis_core
from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.core.security.cas import _STORAGE_USAGE_GENERATION_KEY, _STORAGE_USAGE_KEY
from app.core.storage import capacity, facade

pytestmark = pytest.mark.integration

_REDIS_URL = os.environ.get("AUTH_ATOMICITY_REDIS_URL")


@pytest_asyncio.fixture
async def process_fence_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncEngine:
    """Give each async test an engine bound only to its current event loop."""
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("real PostgreSQL DATABASE_URL is required")

    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    monkeypatch.setattr(capacity, "engine", test_engine)
    try:
        yield test_engine
    finally:
        await test_engine.dispose()


@pytest.fixture
async def redis() -> Redis:  # type: ignore[type-arg]
    if not _REDIS_URL:
        pytest.skip("AUTH_ATOMICITY_REDIS_URL is required")
    if capacity.engine.dialect.name != "postgresql":
        pytest.skip("real PostgreSQL DATABASE_URL is required")

    client = Redis.from_url(_REDIS_URL, decode_responses=True)
    await client.config_set("appendonly", "yes")
    await client.config_set("appendfsync", "always")
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


class _ImmediateStorage:
    def __init__(self) -> None:
        self.objects: dict[str, int] = {}
        self.writer_entered = asyncio.Event()

    async def object_exists(self, key: str) -> bool:
        return key in self.objects

    async def get_object_info(self, key: str) -> dict[str, Any]:
        return {"size": self.objects[key]}

    async def upload_file(
        self,
        file_obj: bytes,
        file_key: str,
        **_kwargs: Any,
    ) -> None:
        self.writer_entered.set()
        self.objects[file_key] = len(file_obj)


async def _redis_time_ms(redis: Redis) -> int:  # type: ignore[type-arg]
    seconds, microseconds = await redis.time()
    return int(seconds) * 1000 + int(microseconds) // 1000


async def test_suspended_owner_cannot_start_s3_after_successor_recovery(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
    process_fence_engine: AsyncEngine,
) -> None:
    """Fence the exact durable-dispatch / pre-S3 suspension interleaving."""
    storage = _ImmediateStorage()
    monkeypatch.setattr(facade, "get_storage", lambda: storage)

    real_dispatch = capacity.dispatch_cas_storage_mutation
    dispatch_is_durable = asyncio.Event()
    allow_dispatch_return = asyncio.Event()

    async def suspend_after_durable_dispatch(
        redis_client: Any,
        mutation_id: str,
        mutation_epoch: int,
    ) -> int:
        result = await real_dispatch(redis_client, mutation_id, mutation_epoch)
        dispatch_is_durable.set()
        await allow_dispatch_return.wait()
        return result

    monkeypatch.setattr(
        capacity,
        "dispatch_cas_storage_mutation",
        suspend_after_durable_dispatch,
    )
    monkeypatch.setattr(redis_core, "redis_client", redis)

    # Keep the owner's Redis renewal asleep while its key is manually removed:
    # this models whole-process suspension, where the renewal coroutine is not
    # running but the PostgreSQL session remains live.
    monkeypatch.setattr(capacity, "_CAS_STORAGE_LOCK_EXPIRE", 30.0)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_LOCK_TIMEOUT", 1.0)

    # Short real dispatch window for a deterministic regression.
    monkeypatch.setattr(capacity, "_CAS_MUTATION_DURABILITY_TIMEOUT_MS", 100)
    monkeypatch.setattr(settings, "cas_mutation_io_timeout_seconds", 0.2)
    monkeypatch.setattr(settings, "cas_mutation_recovery_grace_seconds", 0.2)

    monkeypatch.setattr(capacity, "_CAS_STORAGE_PROCESS_FENCE_TIMEOUT", 0.25)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_PROCESS_FENCE_RETRY", 0.02)

    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)
    await redis.set(capacity._STORAGE_MUTATION_EPOCH_KEY, 0)

    owner = asyncio.create_task(facade.upload_file(b"x" * 17, "cas/process-fence-suspension"))
    await asyncio.wait_for(dispatch_is_durable.wait(), timeout=3)

    assert not storage.writer_entered.is_set()
    intents = await redis.hgetall(capacity._STORAGE_MUTATION_INTENTS_KEY)
    assert len(intents) == 1
    _mutation_id, raw = next(iter(intents.items()))
    dispatched = json.loads(raw)
    assert dispatched["phase"] == "dispatched"

    # Redis ownership disappears while A is suspended.
    await redis.delete(f"lock:{capacity._CAS_STORAGE_MUTATION_LOCK}")

    recover_after_ms = int(dispatched["recover_after_ms"])
    delay = max(0.0, (recover_after_ms - await _redis_time_ms(redis)) / 1000 + 0.1)
    await asyncio.sleep(delay)
    assert await _redis_time_ms(redis) >= recover_after_ms
    assert not storage.writer_entered.is_set()

    # B is now Redis-time eligible to recover, but cannot cross the PostgreSQL
    # process fence while A's live session exists.
    started = monotonic()
    with pytest.raises(BadRequestError, match="physical CAS fence"):
        await capacity.reconcile_cas_storage_usage(redis)
    assert monotonic() - started >= 0.15

    still_pending = await redis.hgetall(capacity._STORAGE_MUTATION_INTENTS_KEY)
    assert still_pending == intents
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) == "1"

    # A resumes while it still owns the DB session fence and can safely perform
    # the canonical write and commit the exact still-live journal.
    allow_dispatch_return.set()
    # The Redis lease was deliberately destroyed above. The PostgreSQL
    # fence makes that loss safe, but redis_lock must still report it.
    with pytest.raises(
        redis_core.RedisConcurrencyError,
        match="Lost lock ownership for storage:cas-physical-usage",
    ):
        await asyncio.wait_for(owner, timeout=3)

    assert storage.writer_entered.is_set()
    assert storage.objects["cas/process-fence-suspension"] == 17
    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 17
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 0
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None


async def test_process_fence_is_released_by_database_connection_death(
    monkeypatch: pytest.MonkeyPatch,
    process_fence_engine: AsyncEngine,
) -> None:
    """Connection death releases the non-expiring fence for crash recovery."""
    monkeypatch.setattr(capacity, "_CAS_STORAGE_PROCESS_FENCE_TIMEOUT", 0.15)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_PROCESS_FENCE_RETRY", 0.02)

    async with process_fence_engine.connect() as dead_owner:
        acquired = bool(
            await dead_owner.scalar(
                sql_text("SELECT pg_try_advisory_lock(:key)"),
                {"key": capacity._CAS_STORAGE_PROCESS_FENCE_KEY},
            )
        )
        await dead_owner.commit()
        assert acquired

        with pytest.raises(BadRequestError, match="physical CAS fence"):
            async with capacity._cas_storage_process_fence():
                pytest.fail("contender acquired a fence still held by a live session")

        # Close the backend session without pg_advisory_unlock(), as a crash
        # would. PostgreSQL must release the session advisory lock server-side.
        await dead_owner.invalidate()

    async with capacity._cas_storage_process_fence():
        pass

@pytest_asyncio.fixture
async def pooled_process_fence_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncEngine:
    """Production-like one-connection pool for session-lock leak regressions."""
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("real PostgreSQL DATABASE_URL is required")

    test_engine = create_async_engine(
        settings.database_url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    monkeypatch.setattr(capacity, "engine", test_engine)
    try:
        yield test_engine
    finally:
        await test_engine.dispose()


async def _assert_fence_available_from_independent_session() -> None:
    """Prove no different PostgreSQL session is blocked by a leaked fence."""
    verifier = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with verifier.connect() as connection:
            acquired = bool(
                await connection.scalar(
                    sql_text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": capacity._CAS_STORAGE_PROCESS_FENCE_KEY},
                )
            )
            await connection.commit()
            assert acquired, "CAS process fence leaked into another pooled PostgreSQL session"
            unlocked = bool(
                await connection.scalar(
                    sql_text("SELECT pg_advisory_unlock(:key)"),
                    {"key": capacity._CAS_STORAGE_PROCESS_FENCE_KEY},
                )
            )
            await connection.commit()
            assert unlocked
    finally:
        await verifier.dispose()


async def test_process_fence_acquisition_cancellation_cannot_leak_pooled_session_lock(
    monkeypatch: pytest.MonkeyPatch,
    pooled_process_fence_engine: AsyncEngine,
) -> None:
    """Ambiguous cancellation after server-side acquisition must kill the DB session."""
    real_scalar = AsyncConnection.scalar
    injected = False

    async def cancel_after_server_scalar(
        self: AsyncConnection,
        statement: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        nonlocal injected
        result = await real_scalar(self, statement, *args, **kwargs)
        if not injected and "pg_try_advisory_lock" in str(statement):
            injected = True
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(AsyncConnection, "scalar", cancel_after_server_scalar)
    with pytest.raises(asyncio.CancelledError):
        async with capacity._cas_storage_process_fence():
            pytest.fail("acquisition cancellation must happen before entering the body")
    assert injected

    monkeypatch.setattr(AsyncConnection, "scalar", real_scalar)
    await _assert_fence_available_from_independent_session()


async def test_process_fence_post_acquisition_commit_failure_cannot_leak_pooled_session_lock(
    monkeypatch: pytest.MonkeyPatch,
    pooled_process_fence_engine: AsyncEngine,
) -> None:
    """A failure after pg_try_advisory_lock succeeds must also destroy the session."""
    real_commit = AsyncConnection.commit
    injected = False

    async def fail_first_commit(self: AsyncConnection) -> None:
        nonlocal injected
        if not injected:
            injected = True
            raise RuntimeError("synthetic post-acquisition commit failure")
        await real_commit(self)

    monkeypatch.setattr(AsyncConnection, "commit", fail_first_commit)
    with pytest.raises(BadRequestError, match="process fence is temporarily unavailable"):
        async with capacity._cas_storage_process_fence():
            pytest.fail("commit failure must happen before entering the body")
    assert injected

    monkeypatch.setattr(AsyncConnection, "commit", real_commit)
    await _assert_fence_available_from_independent_session()

