from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.redis as redis_core
from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.core.security.cas import (
    _STORAGE_USAGE_GENERATION_KEY,
    _STORAGE_USAGE_KEY,
    increment_cas_ref,
)
from app.core.storage import capacity, facade

pytestmark = pytest.mark.integration

_REDIS_URL = os.environ.get("AUTH_ATOMICITY_REDIS_URL")
_REDIS_COMMAND_TIMEOUT_SECONDS = 15.0


async def _bounded_redis_call(label: str, awaitable: Any) -> Any:
    """Fail the integration test instead of hanging forever on a wedged Redis command."""
    try:
        return await asyncio.wait_for(awaitable, timeout=_REDIS_COMMAND_TIMEOUT_SECONDS)
    except TimeoutError:
        message = (
            f"Redis integration timed out after {_REDIS_COMMAND_TIMEOUT_SECONDS:.0f}s while {label}"
        )
        pytest.fail(message, pytrace=False)


@pytest.fixture
async def redis() -> Redis:  # type: ignore[type-arg]
    if not _REDIS_URL:
        pytest.skip("AUTH_ATOMICITY_REDIS_URL is required for this integration test")
    client = Redis.from_url(
        _REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=_REDIS_COMMAND_TIMEOUT_SECONDS,
        retry_on_timeout=False,
    )
    try:
        # The production Redis configuration uses AOF. WAITAOF is part of the
        # physical-mutation safety invariant, so the real-Redis test must exercise it.
        # Redis CONFIG state is server-global, so do not repeatedly re-apply these
        # settings for every function-scoped fixture when they are already active.
        appendfsync = await _bounded_redis_call(
            "reading appendfsync", client.config_get("appendfsync")
        )
        if appendfsync.get("appendfsync") != "always":
            await _bounded_redis_call(
                "setting appendfsync=always", client.config_set("appendfsync", "always")
            )

        appendonly = await _bounded_redis_call(
            "reading appendonly", client.config_get("appendonly")
        )
        if appendonly.get("appendonly") != "yes":
            await _bounded_redis_call("enabling AOF", client.config_set("appendonly", "yes"))

        await _bounded_redis_call("flushing the test database", client.flushdb())
    except BaseException:
        await client.aclose()
        raise

    try:
        yield client
    finally:
        try:
            await _bounded_redis_call(
                "flushing the test database during teardown", client.flushdb()
            )
        finally:
            await client.aclose()


async def test_cas_metadata_creation_does_not_change_physical_usage(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    await redis.set(_STORAGE_USAGE_KEY, 17)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 3)

    await increment_cas_ref(
        redis,
        "d" * 64,
        initial_data={
            "final_key": "cas/real-redis-generation",
            "size": 17,
            "mime_type": "text/plain",
            "file_name": "generation.txt",
        },
        operation_id="integration:cas-metadata:create",
    )

    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 17
    assert int(await redis.get(_STORAGE_USAGE_GENERATION_KEY) or 0) == 3


async def test_missing_usage_cache_persists_generation_zero_and_scans_once(
    redis: Redis,  # type: ignore[type-arg]
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scans = 0

    async def physical_usage() -> int:
        nonlocal scans
        scans += 1
        return 23

    monkeypatch.setattr(capacity, "_physical_cas_storage_usage", physical_usage)

    assert await capacity.get_storage_usage(db_session, redis) == 23
    assert await redis.get(_STORAGE_USAGE_KEY) == "23"
    assert await redis.get(_STORAGE_USAGE_GENERATION_KEY) == "0"
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None

    assert await capacity.get_storage_usage(db_session, redis) == 23
    assert scans == 1


@dataclass(frozen=True)
class _FakeMutationCapability:
    operation: str
    key: str
    recovery_fence_ms: int


class _CapabilityStorageMixin:
    recovery_fence_ms_override: int | None = None

    def _recovery_fence_ms(self, ttl: int) -> int:
        if self.recovery_fence_ms_override is not None:
            return self.recovery_fence_ms_override
        return max(0, ttl * 1000)

    async def presign_cas_put_capability(
        self, file_key: str, *, ttl: int, **_kwargs: Any
    ) -> _FakeMutationCapability:
        return _FakeMutationCapability("put", file_key, self._recovery_fence_ms(ttl))

    async def presign_cas_delete_capability(
        self, file_key: str, *, ttl: int
    ) -> _FakeMutationCapability:
        return _FakeMutationCapability("delete", file_key, self._recovery_fence_ms(ttl))

    async def execute_presigned_mutation(
        self, capability: _FakeMutationCapability, *, body: Any = None
    ) -> None:
        operation, key = capability.operation, capability.key
        if operation == "put":
            assert isinstance(body, (bytes, bytearray, memoryview))
            await self.upload_file(bytes(body), key)  # type: ignore[attr-defined]
            return
        if operation == "delete":
            await self.delete_object(key)  # type: ignore[attr-defined]
            return
        raise AssertionError(f"unexpected fake mutation capability: {operation}")


class _BarrierStorage(_CapabilityStorageMixin):
    def __init__(self, *, fail_after_visible: bool = False) -> None:
        self.objects: dict[str, int] = {}
        self.visible = asyncio.Event()
        self.deleted = asyncio.Event()
        self.allow_completion = asyncio.Event()
        self.allow_delete_completion = asyncio.Event()
        self.fail_after_visible = fail_after_visible

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
        self.objects[file_key] = len(file_obj)
        self.visible.set()
        if self.fail_after_visible:
            raise OSError("lost success after physical write")
        await self.allow_completion.wait()

    async def delete_object(self, file_key: str) -> None:
        self.objects.pop(file_key, None)
        self.deleted.set()
        await self.allow_delete_completion.wait()


async def test_existing_physical_cas_metadata_rebuild_does_not_double_charge(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _BarrierStorage()
    storage.objects["cas/existing"] = 17
    storage.allow_completion.set()
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)
    await redis.set(_STORAGE_USAGE_KEY, 17)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 4)

    await facade.upload_file(b"x" * 17, "cas/existing")
    await increment_cas_ref(
        redis,
        "e" * 64,
        initial_data={
            "final_key": "cas/existing",
            "size": 17,
            "mime_type": "text/plain",
            "file_name": "existing.txt",
        },
        operation_id="integration:cas-metadata:rebuild-existing",
    )

    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 17
    assert int(await redis.get(_STORAGE_USAGE_GENERATION_KEY) or 0) == 5


async def test_physical_cas_write_blocks_reconcile_until_delta_commit(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _BarrierStorage()
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)
    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)

    scans = 0

    async def physical_usage() -> int:
        nonlocal scans
        scans += 1
        return sum(storage.objects.values())

    monkeypatch.setattr(capacity, "_physical_cas_storage_usage", physical_usage)

    writer = asyncio.create_task(facade.upload_file(b"x" * 17, "cas/barrier"))
    await _bounded_redis_call("waiting for upload visible", storage.visible.wait())
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) == "1"

    reconciler = asyncio.create_task(capacity.reconcile_cas_storage_usage(redis))
    await asyncio.sleep(0.05)
    assert not reconciler.done()
    assert scans == 0

    storage.allow_completion.set()
    await writer
    assert await reconciler == 17

    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 17
    assert int(await redis.get(_STORAGE_USAGE_GENERATION_KEY) or 0) == 1
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None
    assert scans == 1


async def test_physical_cas_delete_blocks_reconcile_until_delta_commit(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _BarrierStorage()
    storage.objects["cas/delete-barrier"] = 17
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)
    await redis.set(_STORAGE_USAGE_KEY, 17)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 8)

    scans = 0

    async def physical_usage() -> int:
        nonlocal scans
        scans += 1
        return sum(storage.objects.values())

    monkeypatch.setattr(capacity, "_physical_cas_storage_usage", physical_usage)

    deleter = asyncio.create_task(facade.delete_object("cas/delete-barrier"))
    await _bounded_redis_call("waiting for delete visible", storage.deleted.wait())
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) == "1"

    reconciler = asyncio.create_task(capacity.reconcile_cas_storage_usage(redis))
    await asyncio.sleep(0.05)
    assert not reconciler.done()
    assert scans == 0

    storage.allow_delete_completion.set()
    await deleter
    assert await reconciler == 0

    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 0
    assert int(await redis.get(_STORAGE_USAGE_GENERATION_KEY) or 0) == 9
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None
    assert scans == 1


async def test_lost_success_cas_write_stays_dirty_until_automatic_recovery(
    redis: Redis,  # type: ignore[type-arg]
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambiguous remote failure is fail-closed while fresh, then self-recovers."""
    storage = _BarrierStorage(fail_after_visible=True)
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)
    monkeypatch.setattr(capacity, "_CAS_MUTATION_RECOVERY_STABILITY_SECONDS", 0.01)
    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)

    async def physical_usage() -> int:
        return sum(storage.objects.values())

    monkeypatch.setattr(capacity, "_physical_cas_storage_usage", physical_usage)

    with pytest.raises(OSError, match="lost success"):
        await facade.upload_file(b"y" * 11, "cas/lost-success")

    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) == "1"
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 1
    with pytest.raises(BadRequestError, match="physical CAS mutation resolves"):
        await capacity.get_storage_usage(db_session, redis)

    # Move the persisted recovery deadline into the past without bypassing the
    # production recovery function itself. The next independent owner must use
    # normal autonomous recovery rather than calling the low-level resolver.
    intents = await redis.hgetall(capacity._STORAGE_MUTATION_INTENTS_KEY)
    mutation_id, raw = next(iter(intents.items()))
    payload = json.loads(raw)
    payload["started_at_ms"] = 1
    payload["dispatched_at_ms"] = 2
    payload["recover_after_ms"] = 3
    await redis.hset(capacity._STORAGE_MUTATION_INTENTS_KEY, mutation_id, json.dumps(payload))

    assert await capacity.recover_stale_cas_storage_mutation(redis)
    assert await capacity.get_storage_usage(db_session, redis) == 11
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 0
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None


async def test_ambiguous_cas_writer_failure_does_not_permanently_block_future_mutations(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _BarrierStorage(fail_after_visible=True)
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)
    monkeypatch.setattr(capacity, "_CAS_MUTATION_RECOVERY_STABILITY_SECONDS", 0.01)
    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)

    async def physical_usage() -> int:
        return sum(storage.objects.values())

    monkeypatch.setattr(capacity, "_physical_cas_storage_usage", physical_usage)

    with pytest.raises(OSError, match="lost success"):
        await facade.upload_file(b"a" * 7, "cas/ambiguous-first")

    intents = await redis.hgetall(capacity._STORAGE_MUTATION_INTENTS_KEY)
    mutation_id, raw = next(iter(intents.items()))
    payload = json.loads(raw)
    payload["started_at_ms"] = 1
    payload["dispatched_at_ms"] = 2
    payload["recover_after_ms"] = 3
    await redis.hset(capacity._STORAGE_MUTATION_INTENTS_KEY, mutation_id, json.dumps(payload))

    storage.fail_after_visible = False
    storage.allow_completion.set()
    await facade.upload_file(b"b" * 5, "cas/next-write")

    assert storage.objects["cas/ambiguous-first"] == 7
    assert storage.objects["cas/next-write"] == 5
    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 12
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 0
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None


async def test_promoted_legacy_release_fences_stale_snapshot_with_real_redis(
    redis: Redis,  # type: ignore[type-arg]
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_storage_gb", 1)
    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)
    await capacity.reserve_storage_limit(100, "promoted", redis, db_session)

    snapshot_read = asyncio.Event()
    allow_first_attempt = asyncio.Event()
    calls = 0

    async def controlled_usage(_db: AsyncSession) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            snapshot_read.set()
            await allow_first_attempt.wait()
        return 0

    monkeypatch.setattr(capacity, "_legacy_storage_usage_from_database", controlled_usage)

    reservation = asyncio.create_task(capacity.reserve_storage_limit(50, "new", redis, db_session))
    await _bounded_redis_call("waiting for snapshot read", snapshot_read.wait())
    await capacity.release_promoted_legacy_storage_reservation("promoted", redis)
    allow_first_attempt.set()
    await reservation

    assert calls >= 2
    assert int(await redis.get(capacity.LEGACY_STORAGE_GENERATION_KEY) or 0) == 1


class _LateVisibilityStorage(_CapabilityStorageMixin):
    def __init__(self) -> None:
        self.objects: dict[str, int] = {}
        self.dispatched = asyncio.Event()
        self.make_visible = asyncio.Event()

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
        self.dispatched.set()
        await self.make_visible.wait()
        self.objects[file_key] = len(file_obj)


async def test_cas_reconcile_cannot_clear_dirty_before_lease_lost_remote_write_resolves(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successor cannot certify clean usage while a lease-lost PUT is unresolved."""
    storage = _LateVisibilityStorage()
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_LOCK_EXPIRE", 0.15)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_LOCK_TIMEOUT", 1.0)

    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)
    await redis.set(capacity._STORAGE_MUTATION_EPOCH_KEY, 0)

    writer = asyncio.create_task(facade.upload_file(b"x" * 17, "cas/lease-loss"))
    await _bounded_redis_call("waiting for writer dispatch", storage.dispatched.wait())

    # The object-store request cannot be dispatched before the durable journal.
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 1
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) == "1"

    # Force the lease to be lost while the already-dispatched PUT is blocked.
    await redis.delete(f"lock:{capacity._CAS_STORAGE_MUTATION_LOCK}")
    await asyncio.sleep(0.1)

    # A successor can acquire the lock but the durable intent prevents a clean
    # pre-write scan from being published.
    with pytest.raises(BadRequestError, match="physical CAS mutation resolves"):
        await capacity.reconcile_cas_storage_usage(redis)
    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 0
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) == "1"

    storage.make_visible.set()
    with pytest.raises(redis_core.RedisConcurrencyError):
        await writer

    # The old owner reports its lost lock only after the remote operation has
    # settled and its epoch-bound intent has been resolved exactly.
    assert storage.objects["cas/lease-loss"] == 17
    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 17
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 0
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None


async def test_cas_begin_intent_is_aof_durable_before_writer_dispatch(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _LateVisibilityStorage()
    # Model a signer clock ahead of the object-store Date clock. The capability
    # layer already converted that skew into a duration; the journal must use
    # the complete duration rather than silently falling back to the base TTL.
    storage.recovery_fence_ms_override = (
        capacity._CAS_STORAGE_MUTATION_CAPABILITY_TTL_SECONDS * 1000 + 321_000
    )
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)
    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)

    writer = asyncio.create_task(facade.upload_file(b"z", "cas/aof-before-io"))
    await _bounded_redis_call("waiting for writer dispatch", storage.dispatched.wait())

    # At writer entry the begin script has already run on the same pinned Redis
    # connection as WAITAOF and the journal is present/dirty.
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 1
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) == "1"
    intents = await redis.hgetall(capacity._STORAGE_MUTATION_INTENTS_KEY)
    _mutation_id, raw = next(iter(intents.items()))
    payload = json.loads(raw)
    assert payload["journal_version"] == 3
    assert payload["phase"] == "dispatched"
    assert int(payload["dispatched_at_ms"]) >= int(payload["started_at_ms"])
    recovery_window_ms = int(payload["recover_after_ms"]) - int(payload["dispatched_at_ms"])
    assert storage.recovery_fence_ms_override is not None
    minimum_external_fence_window_ms = (
        storage.recovery_fence_ms_override
        + int(
            (
                settings.cas_mutation_io_timeout_seconds
                + settings.cas_mutation_recovery_grace_seconds
            )
            * 1000
        )
        + capacity._CAS_MUTATION_DURABILITY_TIMEOUT_MS
    )
    assert recovery_window_ms >= minimum_external_fence_window_ms

    storage.make_visible.set()
    await writer
    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 1


class _SlowPreflightLateVisibilityStorage(_LateVisibilityStorage):
    def __init__(self) -> None:
        super().__init__()
        self.preflight_started = asyncio.Event()
        self.allow_preflight = asyncio.Event()
        self._preflight_calls = 0

    async def object_exists(self, key: str) -> bool:
        if self._preflight_calls == 0:
            self._preflight_calls += 1
            self.preflight_started.set()
            await self.allow_preflight.wait()
            return False
        return await super().object_exists(key)


async def test_abandoned_preflight_intent_is_recovered_without_physical_scan(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation_id = "preflight-owner-disappeared"
    async with redis.client() as journal_redis:
        begin = journal_redis.register_script(capacity._STORAGE_BEGIN_CAS_MUTATION_SCRIPT)
        epoch = int(
            await begin(
                keys=[
                    capacity._STORAGE_USAGE_DIRTY_KEY,
                    capacity._STORAGE_MUTATION_EPOCH_KEY,
                    capacity._STORAGE_MUTATION_INTENTS_KEY,
                ],
                args=[mutation_id, "write", "cas/preflight-abandoned"],
                client=journal_redis,
            )
        )
        assert epoch > 0
        await capacity._wait_for_cas_mutation_durability(journal_redis)
    scans = 0

    async def physical_usage() -> int:
        nonlocal scans
        scans += 1
        return 0

    monkeypatch.setattr(capacity, "_physical_cas_storage_usage", physical_usage)
    assert await capacity.recover_stale_cas_storage_mutation(redis)
    assert scans == 0
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 0
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None


async def test_slow_preflight_cannot_make_live_dispatched_write_recoverable(
    redis: Redis,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _SlowPreflightLateVisibilityStorage()
    monkeypatch.setattr(facade, "get_storage", lambda: storage)
    monkeypatch.setattr(redis_core, "redis_client", redis)
    monkeypatch.setattr(settings, "cas_mutation_io_timeout_seconds", 0.6)
    monkeypatch.setattr(settings, "cas_mutation_recovery_grace_seconds", 0.1)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_LOCK_EXPIRE", 0.25)
    monkeypatch.setattr(capacity, "_CAS_STORAGE_LOCK_TIMEOUT", 1.0)
    await redis.set(_STORAGE_USAGE_KEY, 0)
    await redis.set(_STORAGE_USAGE_GENERATION_KEY, 0)
    await redis.set(capacity._STORAGE_MUTATION_EPOCH_KEY, 0)
    writer = asyncio.create_task(facade.upload_file(b"x" * 17, "cas/slow-preflight-dispatch"))
    await _bounded_redis_call("waiting for preflight start", storage.preflight_started.wait())
    intents = await redis.hgetall(capacity._STORAGE_MUTATION_INTENTS_KEY)
    _mutation_id, raw = next(iter(intents.items()))
    preflight = json.loads(raw)
    assert preflight["journal_version"] == 3
    assert preflight["phase"] == "preflight"
    assert "recover_after_ms" not in preflight
    await asyncio.sleep(0.8)  # beyond the vulnerable creation-based 0.7s window
    storage.allow_preflight.set()
    await _bounded_redis_call("waiting for writer dispatch", storage.dispatched.wait())
    intents = await redis.hgetall(capacity._STORAGE_MUTATION_INTENTS_KEY)
    _mutation_id, raw = next(iter(intents.items()))
    dispatched = json.loads(raw)
    assert dispatched["phase"] == "dispatched"
    assert int(dispatched["dispatched_at_ms"]) > int(preflight["started_at_ms"])
    redis_time = await redis.time()
    now_ms = int(redis_time[0]) * 1000 + int(redis_time[1]) // 1000
    assert int(dispatched["recover_after_ms"]) > now_ms
    await redis.delete(f"lock:{capacity._CAS_STORAGE_MUTATION_LOCK}")
    await asyncio.sleep(0.15)
    with pytest.raises(BadRequestError, match="physical CAS mutation resolves"):
        await capacity.reconcile_cas_storage_usage(redis)
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 1
    storage.make_visible.set()
    with pytest.raises(redis_core.RedisConcurrencyError):
        await writer
    assert storage.objects["cas/slow-preflight-dispatch"] == 17
    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 17
    assert int(await redis.hlen(capacity._STORAGE_MUTATION_INTENTS_KEY)) == 0
    assert await redis.get(capacity._STORAGE_USAGE_DIRTY_KEY) is None
