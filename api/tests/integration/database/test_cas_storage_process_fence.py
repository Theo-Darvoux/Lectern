from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from dataclasses import dataclass
from time import monotonic
from typing import Any

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.core.database.redis as redis_core
from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.core.security.cas import _STORAGE_USAGE_GENERATION_KEY, _STORAGE_USAGE_KEY
from app.core.storage import capacity, facade
from app.core.storage.liveness import (
    acquire_storage_lifecycle_xact_lock,
    storage_key_is_live,
    storage_lifecycle_lock,
)
from app.core.storage.s3 import S3Backend
from app.models.upload import Upload

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


@dataclass(frozen=True)
class _ImmediateCapability:
    operation: str
    key: str
    recovery_fence_ms: int


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

    async def presign_cas_put_capability(
        self, file_key: str, *, ttl: int, **_kwargs: Any
    ) -> _ImmediateCapability:
        return _ImmediateCapability(
            operation="put", key=file_key, recovery_fence_ms=max(0, ttl * 1000)
        )

    async def execute_presigned_mutation(
        self, capability: _ImmediateCapability, *, body: Any = None
    ) -> None:
        operation, key = capability.operation, capability.key
        assert operation == "put"
        assert isinstance(body, (bytes, bytearray, memoryview))
        await self.upload_file(bytes(body), key)


@dataclass(frozen=True)
class _ExpiringPutCapability:
    key: str
    expires_at: float
    recovery_fence_ms: int


class _ExternallyFencedStorage(S3Backend):
    """Minimal S3-shaped backend whose mutation authority expires independently."""

    def __init__(self) -> None:
        # Deliberately do not initialize aioboto3: this test exercises the CAS
        # protocol while the live SeaweedFS suite proves real SigV4 expiry.
        self.objects: dict[str, int] = {}
        self.capability_minted = asyncio.Event()
        self.capability_attempted = asyncio.Event()
        self.physical_mutation_started = asyncio.Event()

    async def object_exists(self, key: str) -> bool:
        return key in self.objects

    async def get_object_info(self, key: str) -> dict[str, Any]:
        return {"size": self.objects[key]}

    async def list_objects(self, prefix: str = ""):
        for key, size in sorted(self.objects.items()):
            if key.startswith(prefix):
                yield {"Key": key, "Size": size}

    async def presign_cas_put_capability(
        self,
        file_key: str,
        *,
        ttl: int,
        **_kwargs: Any,
    ) -> _ExpiringPutCapability:
        capability = _ExpiringPutCapability(
            key=file_key,
            expires_at=asyncio.get_running_loop().time() + ttl,
            recovery_fence_ms=ttl * 1000,
        )
        self.capability_minted.set()
        return capability

    async def execute_presigned_mutation(
        self,
        capability: _ExpiringPutCapability,
        *,
        body: Any = None,
    ) -> None:
        self.capability_attempted.set()
        if asyncio.get_running_loop().time() >= capability.expires_at:
            raise RuntimeError("expired external-store mutation capability")
        self.physical_mutation_started.set()
        assert isinstance(body, (bytes, bytearray, memoryview))
        self.objects[capability.key] = len(body)


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
        external_authority_window_ms: int,
    ) -> int:
        result = await real_dispatch(
            redis_client, mutation_id, mutation_epoch, external_authority_window_ms
        )
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
    # This legacy fake backend intentionally has no external mutation
    # capability. Keep the original process-fence regression fast while the
    # dedicated session-death test below covers capability expiry.
    monkeypatch.setattr(capacity, "_CAS_STORAGE_MUTATION_CAPABILITY_TTL_SECONDS", 0)
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


async def test_stale_owner_after_database_session_death_cannot_mutate_cas_after_recovery(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
    process_fence_engine: AsyncEngine,
) -> None:
    """External capability expiry closes the session-death / stale-task race."""
    storage = _ExternallyFencedStorage()
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)

    # Keep A alive after its Redis key is removed, exactly like a suspended
    # process whose renewal coroutine stopped running.
    monkeypatch.setattr(capacity, "_CAS_STORAGE_LOCK_EXPIRE", 30.0)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_LOCK_TIMEOUT", 1.0)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_PROCESS_FENCE_TIMEOUT", 0.5)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_PROCESS_FENCE_RETRY", 0.02)

    # Use a one-second external authority window so the real race can be
    # exercised quickly. Recovery must remain later than capability expiry +
    # the maximum in-flight I/O ambiguity window.
    monkeypatch.setattr(capacity, "_CAS_STORAGE_MUTATION_CAPABILITY_TTL_SECONDS", 1)
    monkeypatch.setattr(capacity, "_CAS_MUTATION_DURABILITY_TIMEOUT_MS", 100)
    monkeypatch.setattr(capacity, "_CAS_MUTATION_RECOVERY_STABILITY_SECONDS", 0.0)
    monkeypatch.setattr(settings, "cas_mutation_io_timeout_seconds", 0.1)
    monkeypatch.setattr(settings, "cas_mutation_recovery_grace_seconds", 0.1)

    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)
    await redis.set(capacity._STORAGE_MUTATION_EPOCH_KEY, 0)

    real_scalar = AsyncConnection.scalar
    owner_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def capture_fence_owner_pid(
        self: AsyncConnection,
        statement: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = await real_scalar(self, statement, *args, **kwargs)
        if bool(result) and "pg_try_advisory_lock" in str(statement) and not owner_pid.done():
            pid = await real_scalar(self, sql_text("SELECT pg_backend_pid()"))
            owner_pid.set_result(int(pid))
        return result

    monkeypatch.setattr(AsyncConnection, "scalar", capture_fence_owner_pid)

    real_dispatch = capacity.dispatch_cas_storage_mutation
    dispatch_is_durable = asyncio.Event()
    allow_stale_owner_to_resume = asyncio.Event()

    async def suspend_after_durable_dispatch(
        redis_client: Any,
        mutation_id: str,
        mutation_epoch: int,
        external_authority_window_ms: int,
    ) -> int:
        recover_after_ms = await real_dispatch(
            redis_client, mutation_id, mutation_epoch, external_authority_window_ms
        )
        dispatch_is_durable.set()
        await allow_stale_owner_to_resume.wait()
        return recover_after_ms

    monkeypatch.setattr(
        capacity,
        "dispatch_cas_storage_mutation",
        suspend_after_durable_dispatch,
    )

    key = "cas/session-death-stale-owner"
    owner = asyncio.create_task(facade.upload_file(b"x" * 17, key))
    await asyncio.wait_for(storage.capability_minted.wait(), timeout=3)
    await asyncio.wait_for(dispatch_is_durable.wait(), timeout=3)
    pid = await asyncio.wait_for(owner_pid, timeout=3)

    assert not storage.capability_attempted.is_set()
    intents = await redis.hgetall(capacity._STORAGE_MUTATION_INTENTS_KEY)
    assert len(intents) == 1
    _mutation_id, raw = next(iter(intents.items()))
    dispatched = json.loads(raw)
    assert dispatched["phase"] == "dispatched"
    dispatched_at_ms = int(dispatched["dispatched_at_ms"])
    recover_after_ms = int(dispatched["recover_after_ms"])
    assert recover_after_ms - dispatched_at_ms >= 1_300

    # Kill the *actual* PostgreSQL backend session that owns A's session-level
    # advisory fence. A itself remains suspended in Python and therefore can
    # later resume as the stale owner from the production failure report.
    killer = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with killer.connect() as connection:
            terminated = bool(
                await connection.scalar(
                    sql_text("SELECT pg_terminate_backend(:pid)"),
                    {"pid": pid},
                )
            )
            await connection.commit()
            assert terminated
    finally:
        await killer.dispose()

    # Simulate A's Redis lease disappearing while A remains suspended.
    await redis.delete(f"lock:{capacity._CAS_STORAGE_MUTATION_LOCK}")

    delay = max(0.0, (recover_after_ms - await _redis_time_ms(redis)) / 1000 + 0.05)
    await asyncio.sleep(delay)
    assert await _redis_time_ms(redis) >= recover_after_ms
    assert not storage.capability_attempted.is_set()

    # B obtains a new PostgreSQL session fence, sees no physical object, and is
    # now entitled to retire A's journal because A's externally enforced
    # mutation authority has already expired.
    assert await capacity.reconcile_cas_storage_usage(redis) == 0
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 0
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None
    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 0

    allow_stale_owner_to_resume.set()
    with pytest.raises(
        (RuntimeError, redis_core.RedisConcurrencyError),
        match="expired external-store mutation capability|Lost lock ownership",
    ):
        await asyncio.wait_for(owner, timeout=3)

    # A did reach the capability executor, proving the stale coroutine resumed,
    # but external authorization expiry stopped it before physical mutation.
    assert storage.capability_attempted.is_set()
    assert not storage.physical_mutation_started.is_set()
    assert key not in storage.objects
    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 0
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


async def _pooled_backend_pid(engine: AsyncEngine) -> int:
    """Return the PostgreSQL backend PID currently owned by the one-slot pool."""
    async with engine.connect() as connection:
        pid = int(await connection.scalar(sql_text("SELECT pg_backend_pid()")))
        await connection.commit()
        return pid


def _block_process_fence_invalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[asyncio.Event, asyncio.Event, asyncio.Event, Any]:
    """Make invalidation observable and controllable without cancelling its child task."""
    real_invalidate = AsyncConnection.invalidate
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def blocked_invalidate(
        self: AsyncConnection,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        entered.set()
        await release.wait()
        try:
            await real_invalidate(self, *args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(AsyncConnection, "invalidate", blocked_invalidate)
    return entered, release, finished, real_invalidate


async def _cancel_twice_during_blocked_invalidation(
    task: asyncio.Task[None],
    *,
    invalidation_entered: asyncio.Event,
    release_invalidation: asyncio.Event,
    invalidation_finished: asyncio.Event,
) -> None:
    """Prove repeated real Task.cancel() cannot abandon fence-session invalidation."""
    task.cancel("first process-fence cancellation")
    await asyncio.wait_for(invalidation_entered.wait(), timeout=3)

    task.cancel("second process-fence cancellation")
    await asyncio.sleep(0)

    assert not task.done(), "repeated cancellation abandoned PostgreSQL invalidation"
    assert not invalidation_finished.is_set()

    release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)
    assert invalidation_finished.is_set()


async def _assert_cancelled_fence_session_was_destroyed(
    engine: AsyncEngine,
    *,
    previous_backend_pid: int,
) -> None:
    """Verify both lock release and physical pooled-session replacement."""
    replacement_backend_pid = await _pooled_backend_pid(engine)
    assert replacement_backend_pid != previous_backend_pid, (
        "ambiguous process-fence cancellation returned the same PostgreSQL session to the pool"
    )
    await _assert_fence_available_from_independent_session()


async def test_process_fence_real_task_cancellation_after_server_acquisition_destroys_session(
    monkeypatch: pytest.MonkeyPatch,
    pooled_process_fence_engine: AsyncEngine,
) -> None:
    """Cancel after PostgreSQL acquired the fence but before scalar() returns to capacity.py."""
    previous_backend_pid = await _pooled_backend_pid(pooled_process_fence_engine)
    real_scalar = AsyncConnection.scalar
    server_acquired = asyncio.Event()
    never_return_scalar = asyncio.Event()

    async def block_after_server_acquisition(
        self: AsyncConnection,
        statement: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = await real_scalar(self, statement, *args, **kwargs)
        if "pg_try_advisory_lock" in str(statement):
            assert result
            server_acquired.set()
            await never_return_scalar.wait()
        return result

    monkeypatch.setattr(AsyncConnection, "scalar", block_after_server_acquisition)
    invalidation_entered, release_invalidation, invalidation_finished, real_invalidate = (
        _block_process_fence_invalidation(monkeypatch)
    )

    async def acquire_fence() -> None:
        async with capacity._cas_storage_process_fence():
            pytest.fail("cancellation must happen before entering the fenced body")

    task = asyncio.create_task(acquire_fence())
    await asyncio.wait_for(server_acquired.wait(), timeout=3)
    await _cancel_twice_during_blocked_invalidation(
        task,
        invalidation_entered=invalidation_entered,
        release_invalidation=release_invalidation,
        invalidation_finished=invalidation_finished,
    )

    monkeypatch.setattr(AsyncConnection, "scalar", real_scalar)
    monkeypatch.setattr(AsyncConnection, "invalidate", real_invalidate)
    await _assert_cancelled_fence_session_was_destroyed(
        pooled_process_fence_engine,
        previous_backend_pid=previous_backend_pid,
    )


async def test_process_fence_real_task_cancellation_after_acquisition_commit_destroys_session(
    monkeypatch: pytest.MonkeyPatch,
    pooled_process_fence_engine: AsyncEngine,
) -> None:
    """Cancel after the acquisition COMMIT reached PostgreSQL but before commit() returns."""
    previous_backend_pid = await _pooled_backend_pid(pooled_process_fence_engine)
    real_commit = AsyncConnection.commit
    server_committed = asyncio.Event()
    never_return_commit = asyncio.Event()
    commit_calls = 0

    async def block_after_first_server_commit(self: AsyncConnection) -> None:
        nonlocal commit_calls
        await real_commit(self)
        commit_calls += 1
        if commit_calls == 1:
            server_committed.set()
            await never_return_commit.wait()

    monkeypatch.setattr(AsyncConnection, "commit", block_after_first_server_commit)
    invalidation_entered, release_invalidation, invalidation_finished, real_invalidate = (
        _block_process_fence_invalidation(monkeypatch)
    )

    async def acquire_fence() -> None:
        async with capacity._cas_storage_process_fence():
            pytest.fail("cancellation must happen before entering the fenced body")

    task = asyncio.create_task(acquire_fence())
    await asyncio.wait_for(server_committed.wait(), timeout=3)
    await _cancel_twice_during_blocked_invalidation(
        task,
        invalidation_entered=invalidation_entered,
        release_invalidation=release_invalidation,
        invalidation_finished=invalidation_finished,
    )

    monkeypatch.setattr(AsyncConnection, "commit", real_commit)
    monkeypatch.setattr(AsyncConnection, "invalidate", real_invalidate)
    await _assert_cancelled_fence_session_was_destroyed(
        pooled_process_fence_engine,
        previous_backend_pid=previous_backend_pid,
    )


async def test_process_fence_real_task_cancellation_after_server_unlock_destroys_session(
    monkeypatch: pytest.MonkeyPatch,
    pooled_process_fence_engine: AsyncEngine,
) -> None:
    """Cancel after pg_advisory_unlock() executed but before scalar() returns."""
    previous_backend_pid = await _pooled_backend_pid(pooled_process_fence_engine)
    real_scalar = AsyncConnection.scalar
    entered_body = asyncio.Event()
    leave_body = asyncio.Event()
    server_unlocked = asyncio.Event()
    never_return_unlock = asyncio.Event()

    async def block_after_server_unlock(
        self: AsyncConnection,
        statement: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = await real_scalar(self, statement, *args, **kwargs)
        if "pg_advisory_unlock" in str(statement):
            assert result
            server_unlocked.set()
            await never_return_unlock.wait()
        return result

    monkeypatch.setattr(AsyncConnection, "scalar", block_after_server_unlock)
    invalidation_entered, release_invalidation, invalidation_finished, real_invalidate = (
        _block_process_fence_invalidation(monkeypatch)
    )

    async def hold_fence() -> None:
        async with capacity._cas_storage_process_fence():
            entered_body.set()
            await leave_body.wait()

    task = asyncio.create_task(hold_fence())
    await asyncio.wait_for(entered_body.wait(), timeout=3)
    leave_body.set()
    await asyncio.wait_for(server_unlocked.wait(), timeout=3)
    await _cancel_twice_during_blocked_invalidation(
        task,
        invalidation_entered=invalidation_entered,
        release_invalidation=release_invalidation,
        invalidation_finished=invalidation_finished,
    )

    monkeypatch.setattr(AsyncConnection, "scalar", real_scalar)
    monkeypatch.setattr(AsyncConnection, "invalidate", real_invalidate)
    await _assert_cancelled_fence_session_was_destroyed(
        pooled_process_fence_engine,
        previous_backend_pid=previous_backend_pid,
    )


async def test_process_fence_real_task_cancellation_after_release_commit_destroys_session(
    monkeypatch: pytest.MonkeyPatch,
    pooled_process_fence_engine: AsyncEngine,
) -> None:
    """Cancel after release COMMIT reached PostgreSQL but before commit() returns."""
    previous_backend_pid = await _pooled_backend_pid(pooled_process_fence_engine)
    real_commit = AsyncConnection.commit
    entered_body = asyncio.Event()
    leave_body = asyncio.Event()
    release_committed = asyncio.Event()
    never_return_release_commit = asyncio.Event()
    commit_calls = 0

    async def block_after_second_server_commit(self: AsyncConnection) -> None:
        nonlocal commit_calls
        await real_commit(self)
        commit_calls += 1
        if commit_calls == 2:
            release_committed.set()
            await never_return_release_commit.wait()

    monkeypatch.setattr(AsyncConnection, "commit", block_after_second_server_commit)
    invalidation_entered, release_invalidation, invalidation_finished, real_invalidate = (
        _block_process_fence_invalidation(monkeypatch)
    )

    async def hold_fence() -> None:
        async with capacity._cas_storage_process_fence():
            entered_body.set()
            await leave_body.wait()

    task = asyncio.create_task(hold_fence())
    await asyncio.wait_for(entered_body.wait(), timeout=3)
    leave_body.set()
    await asyncio.wait_for(release_committed.wait(), timeout=3)
    await _cancel_twice_during_blocked_invalidation(
        task,
        invalidation_entered=invalidation_entered,
        release_invalidation=release_invalidation,
        invalidation_finished=invalidation_finished,
    )

    monkeypatch.setattr(AsyncConnection, "commit", real_commit)
    monkeypatch.setattr(AsyncConnection, "invalidate", real_invalidate)
    await _assert_cancelled_fence_session_was_destroyed(
        pooled_process_fence_engine,
        previous_backend_pid=previous_backend_pid,
    )


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


async def _delete_lifecycle_test_upload(
    sessions: async_sessionmaker,
    upload_id: str,
) -> None:
    async with sessions() as cleanup:
        await cleanup.execute(delete(Upload).where(Upload.upload_id == upload_id))
        await cleanup.commit()


async def test_gc_candidate_waits_for_cas_publisher_then_rechecks_liveness(
    process_fence_engine: AsyncEngine,
) -> None:
    """Reproduce orphan discovery -> concurrent admission -> stale GC deletion attempt."""
    sessions = async_sessionmaker(process_fence_engine, expire_on_commit=False)
    key = f"cas/lifecycle-{uuid.uuid4().hex}"
    upload_id = f"lifecycle-publish-{uuid.uuid4().hex}"
    object_store: dict[str, bytes] = {key: b"old-orphan"}

    # T1: GC legitimately discovers an orphan before either side owns the
    # lifecycle lock. This is only a candidate decision.
    async with sessions() as discovery:
        assert not await storage_key_is_live(discovery, key)

    publisher_committed = asyncio.Event()
    release_publisher = asyncio.Event()
    gc_attempt_started = asyncio.Event()
    gc_acquired = asyncio.Event()

    async def publish_same_cas() -> None:
        # T2/T3: upload owns the session-level lock across physical write and
        # authoritative Upload publication.
        async with storage_lifecycle_lock(sessions, key):
            object_store[key] = b"newly-published"
            async with sessions() as publication:
                publication.add(
                    Upload(
                        upload_id=upload_id,
                        final_key=key,
                        status="clean",
                        filename="same.pdf",
                        cas_ref_count=1,
                    )
                )
                await publication.commit()
            publisher_committed.set()
            await release_publisher.wait()

    async def stale_gc_attempt() -> bool:
        # T4: GC reaches the previously recorded candidate. It must block on
        # the same key until publication is completely visible, then re-check.
        async with sessions() as gc:
            gc_attempt_started.set()
            await acquire_storage_lifecycle_xact_lock(gc, key)
            gc_acquired.set()
            live = await storage_key_is_live(gc, key)
            if not live:
                object_store.pop(key, None)
            await gc.commit()
            return live

    publisher = asyncio.create_task(publish_same_cas())
    gc: asyncio.Task[bool] | None = None
    try:
        await asyncio.wait_for(publisher_committed.wait(), timeout=5)
        gc = asyncio.create_task(stale_gc_attempt())
        await asyncio.wait_for(gc_attempt_started.wait(), timeout=5)
        await asyncio.sleep(0.2)
        assert not gc_acquired.is_set(), "GC crossed the publisher lifecycle lock early"

        release_publisher.set()
        await asyncio.wait_for(publisher, timeout=5)
        assert gc is not None
        assert await asyncio.wait_for(gc, timeout=5) is True
        assert object_store[key] == b"newly-published"
    finally:
        release_publisher.set()
        if not publisher.done():
            await publisher
        if gc is not None and not gc.done():
            gc.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await gc
        await _delete_lifecycle_test_upload(sessions, upload_id)


async def test_gc_lock_forces_later_cas_publisher_to_rewrite_after_delete(
    process_fence_engine: AsyncEngine,
) -> None:
    """Reproduce GC lock first -> uploader blocks -> delete -> uploader rewrites/publishes."""
    sessions = async_sessionmaker(process_fence_engine, expire_on_commit=False)
    key = f"cas/lifecycle-{uuid.uuid4().hex}"
    upload_id = f"lifecycle-rewrite-{uuid.uuid4().hex}"
    object_store: dict[str, bytes] = {key: b"old-orphan"}
    uploader_attempt_started = asyncio.Event()
    uploader_acquired = asyncio.Event()

    async def publish_same_cas() -> None:
        uploader_attempt_started.set()
        async with storage_lifecycle_lock(sessions, key):
            uploader_acquired.set()
            object_store[key] = b"replacement"
            async with sessions() as publication:
                publication.add(
                    Upload(
                        upload_id=upload_id,
                        final_key=key,
                        status="clean",
                        filename="same.pdf",
                        cas_ref_count=1,
                    )
                )
                await publication.commit()

    uploader: asyncio.Task[None] | None = None
    try:
        async with sessions() as gc:
            # T1/T2: GC owns the transaction lock and has freshly established
            # that the candidate is still an orphan.
            await acquire_storage_lifecycle_xact_lock(gc, key)
            assert not await storage_key_is_live(gc, key)

            uploader = asyncio.create_task(publish_same_cas())
            await asyncio.wait_for(uploader_attempt_started.wait(), timeout=5)
            await asyncio.sleep(0.2)
            assert not uploader_acquired.is_set(), "publisher crossed the GC lifecycle lock early"

            # T3: destructive I/O occurs while admission is excluded.
            object_store.pop(key, None)
            await gc.commit()

        # T4/T5: only after deletion commits may the uploader acquire the lock;
        # it rewrites identical content and publishes ownership before releasing.
        assert uploader is not None
        await asyncio.wait_for(uploader, timeout=5)
        assert object_store[key] == b"replacement"
        async with sessions() as verify:
            assert await storage_key_is_live(verify, key)
    finally:
        if uploader is not None and not uploader.done():
            uploader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await uploader
        await _delete_lifecycle_test_upload(sessions, upload_id)


async def test_thumbnail_lifecycle_lock_conflicts_with_prune_transaction(
    process_fence_engine: AsyncEngine,
) -> None:
    """Thumbnail publication uses the same session-vs-transaction fence as CAS."""
    sessions = async_sessionmaker(process_fence_engine, expire_on_commit=False)
    key = f"thumbnails/{uuid.uuid4().hex}/upload.webp"
    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()
    prune_attempt_started = asyncio.Event()
    prune_acquired = asyncio.Event()

    async def hold_thumbnail_publication() -> None:
        async with storage_lifecycle_lock(sessions, key):
            holder_acquired.set()
            await release_holder.wait()

    async def acquire_prune_lock() -> None:
        async with sessions() as prune:
            prune_attempt_started.set()
            await acquire_storage_lifecycle_xact_lock(prune, key)
            prune_acquired.set()
            await prune.rollback()

    holder = asyncio.create_task(hold_thumbnail_publication())
    prune: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(holder_acquired.wait(), timeout=5)
        prune = asyncio.create_task(acquire_prune_lock())
        await asyncio.wait_for(prune_attempt_started.wait(), timeout=5)
        await asyncio.sleep(0.2)
        assert not prune_acquired.is_set(), "thumbnail prune lock failed to serialize"
        release_holder.set()
        await asyncio.wait_for(holder, timeout=5)
        await asyncio.wait_for(prune, timeout=5)
        assert prune_acquired.is_set()
    finally:
        release_holder.set()
        if not holder.done():
            await holder
        if prune is not None and not prune.done():
            prune.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await prune
