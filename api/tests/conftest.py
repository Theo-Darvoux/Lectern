import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database.database import get_db
from app.core.database.redis import get_redis
from app.core.security.scanner import MalwareScanner
from app.main import app
from app.models.base import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _isolate_test_settings():
    from app.config import settings

    orig_env = settings.environment
    orig_bootstrap = settings.bootstrap_token
    orig_bazaar = settings.malwarebazaar_fail_closed
    orig_domains = settings.allowed_domains
    orig_allow_all = settings.allow_all_domains

    settings.bootstrap_token = None
    settings.malwarebazaar_fail_closed = True
    try:
        yield
    finally:
        settings.environment = orig_env
        settings.bootstrap_token = orig_bootstrap
        settings.malwarebazaar_fail_closed = orig_bazaar
        settings.allowed_domains = orig_domains
        settings.allow_all_domains = orig_allow_all


@pytest.fixture(scope="session")
def engine():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=StaticPool, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()

    return engine


@pytest.fixture
async def db_connection(engine) -> AsyncGenerator[Any, None]:
    async with engine.connect() as conn:
        yield conn


@pytest.fixture
async def db_session(db_connection, engine) -> AsyncGenerator[AsyncSession, None]:
    import app.core.database.database as c_db

    orig_factory = c_db.async_session_factory
    c_db.engine = engine

    async with db_connection.begin():
        await db_connection.run_sync(Base.metadata.create_all)

    # Define a factory that always uses the shared connection
    def shared_session_factory(**kwargs: Any) -> AsyncSession:
        kwargs.pop("bind", None)
        return AsyncSession(db_connection, expire_on_commit=False, **kwargs)

    c_db.async_session_factory = shared_session_factory

    session = shared_session_factory()
    session.info["post_commit_jobs"] = []

    yield session

    await session.close()
    async with db_connection.begin():
        await db_connection.run_sync(Base.metadata.drop_all)

    c_db.async_session_factory = orig_factory


@pytest.fixture
def db_lock():
    # asyncio primitives are loop-bound; pytest uses a fresh loop per test.
    return asyncio.Lock()


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.mget = AsyncMock(return_value=[b"0", b"0", None])
    redis.hlen = AsyncMock(return_value=0)
    redis.hgetall = AsyncMock(return_value={})
    redis.time = AsyncMock(return_value=(1_700_000_000, 0))
    redis.execute_command = AsyncMock(return_value=[1, 0])
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    redis.incr = AsyncMock()
    redis.expire = AsyncMock()
    redis.zadd = AsyncMock()
    redis.zcard = AsyncMock(return_value=0)
    redis.zrange = AsyncMock(return_value=[])
    redis.zremrangebyscore = AsyncMock()
    redis.zrem = AsyncMock()
    redis.ltrim = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    redis.publish = AsyncMock()

    from unittest.mock import MagicMock

    pipe = AsyncMock()
    # Redis pipeline commands can be called with or without await depending on the
    # pipeline mode (buffered vs. transaction). AsyncMock handles both; the
    # "coroutine never awaited" RuntimeWarning for non-awaited calls is silenced
    # via filterwarnings in pyproject.toml.
    pipe.set = AsyncMock(return_value=pipe)
    pipe.incr = AsyncMock(return_value=pipe)
    pipe.expire = AsyncMock(return_value=pipe)
    pipe.zadd = AsyncMock(return_value=pipe)
    pipe.zcard = AsyncMock(return_value=pipe)
    pipe.zremrangebyscore = AsyncMock(return_value=pipe)
    pipe.zrem = AsyncMock(return_value=pipe)
    pipe.hset = AsyncMock(return_value=pipe)

    pipe.execute = AsyncMock(return_value=[1, True, 1, True, 0, 0, 0])
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)

    redis.pipeline = MagicMock(return_value=pipe)
    redis.register_script = MagicMock(return_value=AsyncMock(return_value=1))

    single_client_cm = AsyncMock()
    single_client_cm.__aenter__ = AsyncMock(return_value=redis)
    single_client_cm.__aexit__ = AsyncMock(return_value=None)
    redis.client = MagicMock(return_value=single_client_cm)

    lock_mock = AsyncMock()
    lock_mock.__aenter__ = AsyncMock(return_value=lock_mock)
    lock_mock.__aexit__ = AsyncMock(return_value=None)
    redis.lock = MagicMock(return_value=lock_mock)

    return redis


class FakeRedis:
    def __init__(self):
        self.data = {}

    def lock(self, name, timeout=None, sleep=None, **kwargs):
        from unittest.mock import AsyncMock, MagicMock

        lock_mock = MagicMock()
        lock_mock.acquire = AsyncMock(return_value=True)
        lock_mock.extend = AsyncMock(return_value=True)
        lock_mock.release = AsyncMock(return_value=True)
        lock_mock.__aenter__ = AsyncMock(return_value=lock_mock)
        lock_mock.__aexit__ = AsyncMock(return_value=None)
        return lock_mock

    def register_script(self, script):
        async def run(*, keys, args, client=None):
            if "storage_begin_cas_mutation_v3" in script:
                import time

                intents = self.data.setdefault(keys[2], {})
                if intents:
                    return -1
                raw_epoch = self.data.get(keys[1], 0)
                epoch = int(raw_epoch.decode() if isinstance(raw_epoch, bytes) else raw_epoch)
                next_epoch = epoch + 1
                started_at_ms = int(time.time() * 1000)
                self.data[keys[1]] = str(next_epoch).encode()
                intents[str(args[0])] = {
                    "journal_version": 3,
                    "phase": "preflight",
                    "operation": str(args[1]),
                    "file_key": str(args[2]),
                    "started_at_ms": started_at_ms,
                    "epoch": next_epoch,
                }
                self.data[keys[0]] = b"1"
                return next_epoch

            if "storage_dispatch_cas_mutation_v1" in script:
                import time

                raw_epoch = self.data.get(keys[0], 0)
                epoch = int(raw_epoch.decode() if isinstance(raw_epoch, bytes) else raw_epoch)
                if epoch != int(args[1]):
                    return 0
                intents = self.data.setdefault(keys[1], {})
                intent = intents.get(str(args[0]))
                if not isinstance(intent, dict):
                    return -1
                if intent.get("phase") != "preflight":
                    return -3
                dispatched_at_ms = int(time.time() * 1000)
                recover_after_ms = dispatched_at_ms + int(args[2])
                intent["phase"] = "dispatched"
                intent["dispatched_at_ms"] = dispatched_at_ms
                intent["recover_after_ms"] = recover_after_ms
                return recover_after_ms

            if "storage_abort_cas_mutation_v2" in script:
                intents = self.data.setdefault(keys[2], {})
                raw_epoch = self.data.get(keys[1], 0)
                epoch = int(raw_epoch.decode() if isinstance(raw_epoch, bytes) else raw_epoch)
                if epoch != int(args[1]):
                    return 0
                intent = intents.get(str(args[0]))
                if not isinstance(intent, dict):
                    return -1
                if intent.get("phase") != str(args[2]):
                    return -3
                intents.pop(str(args[0]), None)
                self.data[keys[1]] = str(epoch + 1).encode()
                if not intents:
                    self.data.pop(keys[0], None)
                return 1

            if "storage_commit_cas_delta_v3" in script:
                intents = self.data.setdefault(keys[4], {})
                raw_epoch = self.data.get(keys[3], 0)
                epoch = int(raw_epoch.decode() if isinstance(raw_epoch, bytes) else raw_epoch)
                mutation_id = str(args[1])
                intent = intents.get(mutation_id)
                if (
                    epoch != int(args[2])
                    or not isinstance(intent, dict)
                    or intent.get("phase") != "dispatched"
                ):
                    return -4
                if keys[0] not in self.data or keys[1] not in self.data:
                    return -3
                usage = int(self.data[keys[0]])
                generation = int(self.data[keys[1]])
                updated = usage + int(args[0])
                if updated < 0:
                    return -1
                self.data[keys[0]] = str(updated).encode()
                self.data[keys[1]] = str(generation + 1).encode()
                intents.pop(mutation_id, None)
                self.data[keys[3]] = str(epoch + 1).encode()
                if not intents:
                    self.data.pop(keys[2], None)
                return updated

            if "storage_resolve_cas_mutation_v2" in script:
                intents = self.data.setdefault(keys[4], {})
                raw_epoch = self.data.get(keys[3], 0)
                epoch = int(raw_epoch.decode() if isinstance(raw_epoch, bytes) else raw_epoch)
                mutation_id = str(args[0])
                intent = intents.get(mutation_id)
                if (
                    epoch != int(args[1])
                    or not isinstance(intent, dict)
                    or intent.get("phase") != "dispatched"
                ):
                    return 0
                generation = int(self.data.get(keys[1], 0))
                physical_usage = int(args[2])
                self.data[keys[0]] = str(physical_usage).encode()
                self.data[keys[1]] = str(generation + 1).encode()
                intents.pop(mutation_id, None)
                self.data[keys[3]] = str(epoch + 1).encode()
                if not intents:
                    self.data.pop(keys[2], None)
                return physical_usage

            if "storage_reconcile_cas_usage_v3" in script:
                intents = self.data.setdefault(keys[4], {})
                if intents:
                    return -3
                expected_generation = int(args[0])
                expected_epoch = int(args[1])
                physical_usage = int(args[2])
                raw_generation = self.data.get(keys[1], 0)
                current_generation = int(
                    raw_generation.decode() if isinstance(raw_generation, bytes) else raw_generation
                )
                raw_epoch = self.data.get(keys[3], 0)
                current_epoch = int(
                    raw_epoch.decode() if isinstance(raw_epoch, bytes) else raw_epoch
                )
                if current_generation != expected_generation or current_epoch != expected_epoch:
                    return 0
                self.data[keys[0]] = str(physical_usage).encode()
                self.data[keys[1]] = str(expected_generation).encode()
                self.data.pop(keys[2], None)
                return 1

            if "auth_store_login_challenge_v3" in script:

                def _text(value):
                    return value.decode() if isinstance(value, bytes) else value

                previous = _text(self.data.get(keys[1]))
                if previous:
                    self.data.pop(f"auth:magic:{previous}", None)

                code = str(args[0])
                email = str(args[1])
                token = str(args[2])
                generation = str(args[3])
                self.data[keys[0]] = code.encode()
                self.data[keys[2]] = email.encode()
                self.data[keys[1]] = token.encode()
                self.data[keys[3]] = generation.encode()
                return 1

            if "auth_verify_code_v2" in script:

                def _text(value):
                    return value.decode() if isinstance(value, bytes) else value

                stored = _text(self.data.get(keys[0]))
                expected = str(args[0])
                if not stored or stored != expected:
                    return [0]
                generation = _text(self.data.get(keys[2])) or "0"
                magic_token = _text(self.data.get(keys[1]))
                self.data.pop(keys[0], None)
                self.data.pop(keys[2], None)
                if magic_token:
                    self.data.pop(f"auth:magic:{magic_token}", None)
                    self.data.pop(keys[1], None)
                return [1, generation]

            if "auth_verify_magic_v3" in script:

                def _text(value):
                    return value.decode() if isinstance(value, bytes) else value

                email = _text(self.data.get(keys[0]))
                expected_email = str(args[0])
                token = str(args[1])
                if not email or email != expected_email:
                    return [0]
                current_ref = _text(self.data.get(keys[1]))
                if current_ref != token:
                    self.data.pop(keys[0], None)
                    return [0]
                generation = _text(self.data.get(keys[3])) or "0"
                self.data.pop(keys[0], None)
                self.data.pop(keys[1], None)
                self.data.pop(keys[2], None)
                self.data.pop(keys[3], None)
                return [1, generation]

            if "auth_store_password_reset_v1" in script:

                def _text(value):
                    return value.decode() if isinstance(value, bytes) else value

                previous = _text(self.data.get(keys[2]))
                if previous:
                    self.data.pop(f"auth:password_reset:{previous}", None)
                    self.data.pop(f"auth:password_reset_gen:{previous}", None)
                self.data[keys[0]] = str(args[0]).encode()
                self.data[keys[1]] = str(args[1]).encode()
                self.data[keys[2]] = str(args[2]).encode()
                return 1

            if "auth_consume_password_reset_v1" in script:

                def _text(value):
                    return value.decode() if isinstance(value, bytes) else value

                email = _text(self.data.get(keys[0]))
                generation = _text(self.data.get(keys[1]))
                current_digest = _text(self.data.get(keys[2]))
                if not email or generation is None or current_digest != str(args[0]):
                    self.data.pop(keys[0], None)
                    self.data.pop(keys[1], None)
                    return [0]
                self.data.pop(keys[0], None)
                self.data.pop(keys[1], None)
                self.data.pop(keys[2], None)
                return [1, email, generation]

            if "holder_id" in script and "ZREMRANGEBYSCORE" in script:
                import time

                sem_key = keys[0]
                limit = int(args[0])
                holder_id = str(args[1])
                operation = str(args[2])
                expire_ms = int(args[4])
                now_ms = int(time.monotonic() * 1000)
                holders = self.data.setdefault(sem_key, {})
                expired = [holder for holder, deadline in holders.items() if deadline <= now_ms]
                for holder in expired:
                    holders.pop(holder, None)
                if operation == "renew":
                    if holder_id not in holders:
                        return 0
                    holders[holder_id] = now_ms + expire_ms
                    return 1
                if holder_id in holders or len(holders) < limit:
                    holders[holder_id] = now_ms + expire_ms
                    return 1
                return 0

            if "requested_size" in script:
                reservation_id = str(args[0])
                requested_size = int(args[1])
                capacity = int(args[4])
                expected_generation = int(args[5]) if len(args) > 5 else 0
                legacy_snapshot = int(args[6]) if len(args) > 6 else 0
                generation_raw = self.data.get(keys[5], 0) if len(keys) > 5 else 0
                generation = int(
                    generation_raw.decode() if isinstance(generation_raw, bytes) else generation_raw
                )
                if generation != expected_generation:
                    return -2
                self.data[keys[4]] = str(legacy_snapshot).encode()
                usage_raw = self.data.get(keys[3], 0)
                usage = int(usage_raw.decode() if isinstance(usage_raw, bytes) else usage_raw)
                sizes = self.data.setdefault(keys[1], {})
                previous = int(sizes.get(reservation_id, 0))
                total_raw = self.data.get(keys[2], 0)
                total = int(total_raw.decode() if isinstance(total_raw, bytes) else total_raw)
                if usage + legacy_snapshot + total - previous + requested_size > capacity:
                    self.data[keys[2]] = total
                    return 0
                sizes[reservation_id] = requested_size
                self.data[keys[2]] = total - previous + requested_size
                return 1

            if "generation_key" in script and "INCR" in script:
                reservation_id = str(args[0])
                generation_raw = self.data.get(keys[3], 0)
                generation = (
                    int(
                        generation_raw.decode()
                        if isinstance(generation_raw, bytes)
                        else generation_raw
                    )
                    + 1
                )
                self.data[keys[3]] = str(generation).encode()
                self.data.pop(keys[4], None)
                sizes = self.data.setdefault(keys[1], {})
                released = int(sizes.pop(reservation_id, 0))
                total_raw = self.data.get(keys[2], 0)
                total = int(total_raw.decode() if isinstance(total_raw, bytes) else total_raw)
                self.data[keys[2]] = max(0, total - released)
                return released

            reservation_id = str(args[0])
            sizes = self.data.setdefault(keys[1], {})
            released = int(sizes.pop(reservation_id, 0))
            self.data[keys[2]] = max(0, int(self.data.get(keys[2], 0)) - released)
            return released

        return run

    async def hset(self, name, key=None, value=None, mapping=None):
        if name not in self.data:
            self.data[name] = {}

        # redis-py 4.0+ signature: hset(name, key=None, value=None, mapping=None)
        # If mapping is passed as second positional argument:
        if key is not None and value is None and mapping is None and isinstance(key, dict):
            mapping = key
            key = None

        if mapping:
            for k, v in mapping.items():
                self.data[name][k.encode() if isinstance(k, str) else k] = (
                    str(v).encode() if isinstance(v, (str, int)) else v
                )
        elif key is not None:
            self.data[name][key.encode() if isinstance(key, str) else key] = (
                str(value).encode() if isinstance(value, (str, int)) else value
            )

    async def hgetall(self, name):
        return self.data.get(name, {})

    async def hget(self, name, key):
        return self.data.get(name, {}).get(key)

    async def hlen(self, name):
        value = self.data.get(name, {})
        return len(value) if isinstance(value, dict) else 0

    def client(self):
        parent = self

        class _SingleConnectionContext:
            async def __aenter__(self):
                return parent

            async def __aexit__(self, exc_type, exc, tb):
                return None

        return _SingleConnectionContext()

    async def time(self):
        import time

        now = time.time()
        seconds = int(now)
        return seconds, int((now - seconds) * 1_000_000)

    async def get(self, name):
        return self.data.get(name)

    async def set(self, name, value, ex=None, nx=False, keepttl=False):
        if nx and name in self.data:
            return False
        self.data[name] = str(value).encode() if isinstance(value, (str, int)) else value
        return True

    async def setex(self, name, time, value):
        return await self.set(name, value, ex=time)

    async def expire(self, name, time):
        pass

    async def delete(self, *names):
        deleted = 0
        for name in names:
            if name in self.data:
                deleted += 1
                self.data.pop(name, None)
        return deleted

    async def publish(self, channel, message):
        pass

    async def mget(self, *names):
        return [self.data.get(name) for name in names]

    async def zadd(self, name, mapping):
        if name not in self.data:
            self.data[name] = {}
        for k, v in mapping.items():
            self.data[name][k] = v

    async def zcard(self, name):
        return len(self.data.get(name, {}))

    async def zrange(self, name, start, end, withscores=False):
        d = self.data.get(name, {})
        # Sort by value (score)
        sorted_keys = sorted(d.keys(), key=lambda k: d[k])
        res = sorted_keys[start:] if end == -1 else sorted_keys[start : end + 1]

        if withscores:
            return [(k, d[k]) for k in res]
        return res

    async def zrem(self, name, *members):
        d = self.data.get(name, {})
        count = 0
        for m in members:
            if m in d:
                del d[m]
                count += 1
        return count

    async def incr(self, name):
        val = int(self.data.get(name, 0)) + 1
        self.data[name] = str(val).encode()
        return val

    async def decr(self, name):
        val = int(self.data.get(name, 0)) - 1
        self.data[name] = str(val).encode()
        return val

    async def rpush(self, name, value):
        if name not in self.data:
            self.data[name] = []
        if not isinstance(self.data[name], list):
            self.data[name] = []
        encoded = str(value).encode() if isinstance(value, (str, int)) else value
        self.data[name].append(encoded)
        return len(self.data[name])

    async def llen(self, name):
        return len(self.data.get(name, []))

    async def lrange(self, name, start, end):
        full = self.data.get(name, [])
        if end == -1:
            return full[start:]
        return full[start : end + 1]

    async def ltrim(self, name, start, end):
        lst = self.data.get(name, [])
        if not isinstance(lst, list):
            return
        # Redis LTRIM keeps elements from start to end inclusive.
        if end == -1:
            self.data[name] = lst[start:]
        else:
            self.data[name] = lst[start : end + 1]

    async def exists(self, name):
        return 1 if name in self.data else 0

    async def eval(self, script, numkeys, *keys_and_args):
        # Very limited eval for our CAS scripts
        import json

        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        key = keys[0]
        raw = await self.get(key)
        raw_str = raw.decode() if isinstance(raw, bytes) else raw

        if "ref_count" in script:
            is_incr = " + 1" in script
            marker_index = 2 if is_incr else 1
            marker_key = keys[marker_index] if len(keys) > marker_index else None
            if marker_key is not None and await self.exists(marker_key):
                if not raw_str:
                    return 0
                return json.loads(raw_str).get("ref_count", 0)

            if not raw_str:
                if is_incr and args:
                    # INCR with initial_data (ARGV[1]): create new entry
                    data = json.loads(args[0])
                    data["ref_count"] = 1
                    await self.set(key, json.dumps(data))
                    # Physical storage usage is owned by the storage facade,
                    # not reconstructed from evictable CAS metadata.
                    if marker_key is not None:
                        await self.set(marker_key, "1")
                    return 1
                return -1
            data = json.loads(raw_str)
            if is_incr:
                data["ref_count"] = (data.get("ref_count") or 1) + 1
                await self.set(key, json.dumps(data))
            else:
                count = (data.get("ref_count") or 1) - 1
                if count <= 0:
                    await self.delete(key)
                    if marker_key is not None:
                        await self.set(marker_key, "1")
                    return 0
                data["ref_count"] = count
                await self.set(key, json.dumps(data))
            if marker_key is not None:
                await self.set(marker_key, "1")
            return data["ref_count"]
        return 0

    async def execute_command(self, *args):
        if str(args[0]).upper() == "WAITAOF":
            return [1, 0]
        if args[0] == "GETDEL":
            val = await self.get(args[1])
            await self.delete(args[1])
            return val
        return None

    async def scan(self, cursor, match=None, count=None):
        keys = list(self.data.keys())
        if match:
            import fnmatch

            keys = [k for k in keys if fnmatch.fnmatch(k, match)]
        return 0, keys

    async def keys(self, pattern):
        import fnmatch

        return [k for k in self.data if fnmatch.fnmatch(k, pattern)]

    async def scan_iter(self, pattern):
        import fnmatch

        for k in list(self.data.keys()):
            if fnmatch.fnmatch(k, pattern):
                yield k

    async def sadd(self, name, *members):
        if name not in self.data:
            self.data[name] = set()
        if isinstance(self.data[name], set):
            for m in members:
                self.data[name].add(m)
        return len(members)

    async def srem(self, name, *members):
        s = self.data.get(name, set())
        if isinstance(s, set):
            for m in members:
                s.discard(m)
        return 0

    async def smembers(self, name):
        return self.data.get(name, set())

    def pubsub(self):
        ps = AsyncMock()

        async def _listen():
            # Mock empty stream
            if False:
                yield None

        ps.listen = _listen
        ps.subscribe = AsyncMock()
        ps.unsubscribe = AsyncMock()
        ps.reset = AsyncMock()
        return ps


@pytest.fixture
def fake_redis_setup(mock_redis, monkeypatch):
    fr = FakeRedis()
    fr.data["storage:total_usage_bytes"] = b"0"
    fr.data["storage:total_usage_generation"] = b"0"

    # Route-level tests use an AsyncMock Redis wrapper for call assertions. Run
    # the real semaphore implementation against the backing FakeRedis so lease
    # ownership and renewal semantics are deterministic instead of depending on
    # nested AsyncMock return values.
    from app.core.database.redis import redis_semaphore as real_redis_semaphore

    @asynccontextmanager
    async def fake_route_semaphore(
        _redis,
        sem_name,
        limit,
        timeout=60.0,
        retry_interval=0.2,
        expire=300,
    ):
        async with real_redis_semaphore(
            fr,
            sem_name,
            limit,
            timeout=timeout,
            retry_interval=retry_interval,
            expire=expire,
        ):
            yield

    monkeypatch.setattr("app.routers.tus.redis_semaphore", fake_route_semaphore)
    mock_redis.hset.side_effect = fr.hset
    mock_redis.hgetall.side_effect = fr.hgetall
    mock_redis.hget.side_effect = fr.hget
    mock_redis.hlen.side_effect = fr.hlen
    mock_redis.get.side_effect = fr.get
    mock_redis.set.side_effect = fr.set
    mock_redis.setex.side_effect = fr.setex
    mock_redis.expire.side_effect = fr.expire
    mock_redis.delete.side_effect = fr.delete
    mock_redis.mget.side_effect = fr.mget
    mock_redis.zadd.side_effect = fr.zadd
    mock_redis.zcard.side_effect = fr.zcard
    mock_redis.zrange.side_effect = fr.zrange
    mock_redis.zrem.side_effect = fr.zrem
    mock_redis.incr.side_effect = fr.incr
    mock_redis.decr.side_effect = fr.decr
    mock_redis.rpush.side_effect = fr.rpush
    mock_redis.llen.side_effect = fr.llen
    mock_redis.lrange.side_effect = fr.lrange
    mock_redis.ltrim.side_effect = fr.ltrim
    mock_redis.eval.side_effect = fr.eval
    mock_redis.scan.side_effect = fr.scan
    mock_redis.keys.side_effect = fr.keys
    mock_redis.exists.side_effect = fr.exists
    mock_redis.pubsub.side_effect = fr.pubsub
    mock_redis.sadd.side_effect = fr.sadd
    mock_redis.srem.side_effect = fr.srem
    mock_redis.smembers.side_effect = fr.smembers
    mock_redis.execute_command.side_effect = fr.execute_command
    mock_redis.register_script.side_effect = fr.register_script
    return fr


@pytest.fixture
def mock_arq_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock(return_value=None)
    return pool


@pytest.fixture
async def client(
    db_connection,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
    mock_arq_pool: AsyncMock,
    db_lock: asyncio.Lock,
) -> AsyncGenerator[AsyncClient, None]:

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        import app.core.database.redis as redis_core
        from app.core.database.post_commit import (
            PostCommitKey,
            finalize_transaction_callbacks,
            rollback_transaction_callbacks,
        )
        from app.core.events.coalesce import coalesce_index_jobs
        from app.core.events.sse import broadcast_to_topic, broadcast_to_user

        # Serialize access to the shared connection to prevent SQLite transaction conflicts
        async with db_lock, AsyncSession(db_connection, expire_on_commit=False) as session:
            session.info[PostCommitKey.JOBS] = []
            session.info[PostCommitKey.MANAGED_TRANSACTION] = True
            try:
                yield session
                await session.commit()
                await finalize_transaction_callbacks(session)

                for topic, event in session.info.pop(PostCommitKey.SSE, []):
                    broadcast_to_topic(topic, event)
                for user_id, event in session.info.pop(PostCommitKey.USER_SSE, []):
                    broadcast_to_user(user_id, event)

                jobs = session.info.get(PostCommitKey.JOBS, [])
                if jobs:
                    db_session.info.setdefault(PostCommitKey.JOBS, []).extend(jobs)
                    if redis_core.arq_pool:
                        coalesced = coalesce_index_jobs(jobs)
                        for job in coalesced:
                            job_args = list(job[1:])
                            job_kwargs = {}
                            if job_args and isinstance(job_args[-1], dict):
                                encoded = job_args[-1].get("__outbox_kwargs__")
                                if isinstance(encoded, dict):
                                    job_kwargs = encoded
                                    job_args.pop()
                            await redis_core.arq_pool.enqueue_job(job[0], *job_args, **job_kwargs)
            except BaseException:
                await session.rollback()
                await rollback_transaction_callbacks(session)
                raise
            finally:
                session.info.pop(PostCommitKey.MANAGED_TRANSACTION, None)
                session.info.pop(PostCommitKey.TRANSACTION_COMMIT_CALLBACKS, None)
                session.info.pop(PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS, None)

    async def override_get_redis() -> AsyncGenerator[AsyncMock, None]:
        yield mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    if not hasattr(app.state, "scanner"):
        app.state.scanner = MalwareScanner()

    transport = ASGITransport(app=app)
    with (
        patch("app.core.database.redis.arq_pool", mock_arq_pool),
        patch("app.core.database.redis.redis_client", mock_redis),
        # Route tests are hermetic and do not run an object-storage service.
        # Model an initialized empty CAS bucket here; dedicated capacity tests
        # exercise real cache-miss reconciliation separately.
        patch(
            "app.core.storage.capacity._physical_cas_storage_usage",
            new=AsyncMock(return_value=0),
        ),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()
