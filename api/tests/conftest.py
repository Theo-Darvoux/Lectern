import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.redis import get_redis
from app.core.scanner import MalwareScanner
from app.main import app
from app.models.base import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


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
    import app.core.database as c_db

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

    from app.routers.upload import helpers as u_helpers

    orig_helpers_factory = u_helpers.async_session_factory
    u_helpers.async_session_factory = shared_session_factory

    yield session

    await session.close()
    async with db_connection.begin():
        await db_connection.run_sync(Base.metadata.drop_all)

    c_db.async_session_factory = orig_factory
    u_helpers.async_session_factory = orig_helpers_factory


@pytest.fixture(scope="session")
def db_lock():
    return asyncio.Lock()


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    redis.incr = AsyncMock()
    redis.expire = AsyncMock()
    redis.zadd = AsyncMock()
    redis.zcard = AsyncMock(return_value=0)
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

    lock_mock = AsyncMock()
    lock_mock.__aenter__ = AsyncMock(return_value=lock_mock)
    lock_mock.__aexit__ = AsyncMock(return_value=None)
    redis.lock = MagicMock(return_value=lock_mock)

    return redis


class FakeRedis:
    def __init__(self):
        self.data = {}

    def lock(self, name, timeout=None):
        from unittest.mock import AsyncMock

        lock_mock = AsyncMock()
        lock_mock.__aenter__ = AsyncMock(return_value=lock_mock)
        lock_mock.__aexit__ = AsyncMock(return_value=None)
        return lock_mock

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

    async def get(self, name):
        return self.data.get(name)

    async def set(self, name, value, ex=None, nx=False):
        if nx and name in self.data:
            return False
        self.data[name] = str(value).encode() if isinstance(value, (str, int)) else value
        return True

    async def setex(self, name, time, value):
        return await self.set(name, value, ex=time)

    async def expire(self, name, time):
        pass

    async def delete(self, name):
        self.data.pop(name, None)

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

        # Lua: local raw = redis.call('GET', KEYS[1])
        key = keys_and_args[0]
        raw = await self.get(key)
        raw_str = raw.decode() if isinstance(raw, bytes) else raw

        if "ref_count" in script:
            is_incr = " + 1" in script
            if not raw_str:
                if is_incr and len(keys_and_args) > 1:
                    # INCR with initial_data (ARGV[1]): create new entry
                    data = json.loads(keys_and_args[1])
                    data["ref_count"] = 1
                    await self.set(key, json.dumps(data))
                    return 1
                return 0
            data = json.loads(raw_str)
            if is_incr:
                data["ref_count"] = (data.get("ref_count") or 1) + 1
                await self.set(key, json.dumps(data))
                return data["ref_count"]
            else:
                count = (data.get("ref_count") or 1) - 1
                if count <= 0:
                    await self.delete(key)
                    return 0
                data["ref_count"] = count
                await self.set(key, json.dumps(data))
                return count
        return 0

    async def execute_command(self, *args):
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
def fake_redis_setup(mock_redis):
    fr = FakeRedis()
    mock_redis.hset.side_effect = fr.hset
    mock_redis.hgetall.side_effect = fr.hgetall
    mock_redis.hget.side_effect = fr.hget
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
        import app.core.redis as redis_core
        from app.core.database import _coalesce_index_jobs

        # Serialize access to the shared connection to prevent SQLite transaction conflicts
        async with db_lock, AsyncSession(db_connection, expire_on_commit=False) as session:
            session.info["post_commit_jobs"] = []
            try:
                yield session
                await session.commit()

                jobs = session.info.get("post_commit_jobs", [])
                if jobs:
                    db_session.info.setdefault("post_commit_jobs", []).extend(jobs)
                    if redis_core.arq_pool:
                        coalesced = _coalesce_index_jobs(jobs)
                        for job in coalesced:
                            await redis_core.arq_pool.enqueue_job(*job)
            except Exception:
                await session.rollback()
                raise

    async def override_get_redis() -> AsyncGenerator[AsyncMock, None]:
        yield mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    if not hasattr(app.state, "scanner"):
        app.state.scanner = MalwareScanner()

    transport = ASGITransport(app=app)
    with (
        patch("app.core.redis.arq_pool", mock_arq_pool),
        patch("app.core.redis.redis_client", mock_redis),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()
