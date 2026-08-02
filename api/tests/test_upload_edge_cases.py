import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_upload import _auth_headers, _create_user, _make_pdf_file


class FakePipeline:
    def __init__(self, state):
        self.state = state
        self.commands = []

    def zadd(self, key, mapping):
        self.commands.append(("zadd", key, mapping))
        return self

    def zcard(self, key):
        self.commands.append(("zcard", key))
        return self

    def incr(self, key):
        self.commands.append(("incr", key))
        return self

    def expire(self, key, time, **kwargs):
        self.commands.append(("expire", key, time))
        return self

    def get(self, key):
        self.commands.append(("get", key))
        return self

    def __await__(self):
        async def _awaitable():
            return self

        return _awaitable().__await__()

    async def execute(self):
        results = []
        for cmd, *args in self.commands:
            if cmd == "zadd":
                key, mapping = args
                if key not in self.state:
                    self.state[key] = {}
                self.state[key].update(mapping)
                results.append(len(mapping))
            elif cmd == "zcard":
                key = args[0]
                results.append(len(self.state.get(key, {})))
            elif cmd == "incr":
                key = args[0]
                val = self.state.get(key, 0)
                self.state[key] = val + 1
                results.append(self.state[key])
            elif cmd == "expire":
                results.append(True)
            elif cmd == "get":
                key = args[0]
                results.append(self.state.get(key))
        self.commands.clear()
        return results

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        pass


class LocalFakeRedis(AsyncMock):
    def __init__(self, state, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state = state

    def pipeline(self, **kwargs):
        return FakePipeline(self.state)

    async def zremrangebyscore(self, key, min, max):
        pass

    async def zrem(self, key, member):
        if key in self.state and member in self.state[key]:
            del self.state[key][member]

    async def zadd(self, key, mapping):
        if key not in self.state:
            self.state[key] = {}
        self.state[key].update(mapping)

    async def zcard(self, key):
        return len(self.state.get(key, {}))

    async def get(self, key):
        return None

    async def set(self, key, val, **kwargs):
        pass

    async def hset(self, key, mapping=None, **kwargs):
        if key not in self.state:
            self.state[key] = {}
        if mapping:
            for k, v in mapping.items() if isinstance(mapping, dict) else []:
                self.state[key][k] = v

    async def expire(self, key, ttl):
        pass

    async def sadd(self, key, *members):
        if key not in self.state:
            self.state[key] = set()
        if isinstance(self.state[key], set):
            for m in members:
                self.state[key].add(m)

    async def srem(self, key, *members):
        s = self.state.get(key, set())
        if isinstance(s, set):
            for m in members:
                s.discard(m)

    async def scard(self, key):
        s = self.state.get(key, set())
        return len(s) if isinstance(s, set) else 0

    async def smembers(self, key):
        return self.state.get(key, set())

    async def exists(self, key):
        return 1 if key in self.state else 0

    async def hgetall(self, key):
        return self.state.get(key, {})

    async def incr(self, key):
        val = self.state.get(key, 0) + 1
        self.state[key] = val
        return val

    async def delete(self, key):
        self.state.pop(key, None)


@pytest.mark.asyncio
@patch("app.routers.upload.direct.get_s3_client")
async def test_upload_quota_race_condition(
    mock_s3_cm,
    client: AsyncClient,
    db_session: AsyncSession,
    mock_arq_pool: AsyncMock,
) -> None:
    """Test that concurrent uploads do not bypass the quota."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    import typing

    state: dict[str, typing.Any] = {}
    fake_redis = LocalFakeRedis(state)

    with (
        patch("app.routers.upload.helpers.MAX_PENDING_UPLOADS", 2),
        patch("app.routers.upload.direct._reserve_storage_limit", new_callable=AsyncMock),
    ):
        from app.core.database.redis import get_redis
        from app.main import app

        async def override_get_redis():
            yield fake_redis

        app.dependency_overrides[get_redis] = override_get_redis

        try:
            reqs = [
                client.post("/api/upload", files=_make_pdf_file(), headers=_auth_headers(user))
                for _ in range(5)
            ]
            resps = await asyncio.gather(*reqs)

            successes = sum(1 for r in resps if r.status_code == 202)
            failures = sum(
                1 for r in resps if r.status_code == 400 and "Too many pending" in r.text
            )

            assert successes == 2, f"Expected 2 successes, got {successes}"
            assert failures == 3, f"Expected 3 failures, got {failures}"
        finally:
            app.dependency_overrides.pop(get_redis, None)


@pytest.mark.asyncio
async def test_presigned_upload_quota_reservation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    import typing

    state: dict[str, typing.Any] = {}
    fake_redis = LocalFakeRedis(state)

    with (
        patch("app.routers.upload.helpers.MAX_PENDING_UPLOADS", 1),
        patch("app.routers.upload.presigned._reserve_storage_limit", new_callable=AsyncMock),
    ):
        from app.core.database.redis import get_redis
        from app.main import app

        app.dependency_overrides[get_redis] = lambda: fake_redis

        try:
            resp1 = await client.post(
                "/api/upload/init",
                json={"filename": "1.pdf", "size": 1024, "mime_type": "application/pdf"},
                headers=_auth_headers(user),
            )
            assert resp1.status_code == 200

            resp2 = await client.post(
                "/api/upload/init",
                json={"filename": "2.pdf", "size": 1024, "mime_type": "application/pdf"},
                headers=_auth_headers(user),
            )
            assert resp2.status_code == 400
            assert "Too many pending" in resp2.text
        finally:
            app.dependency_overrides.pop(get_redis, None)


@pytest.mark.asyncio
async def test_tus_create_quota_reservation(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    import typing

    state: dict[str, typing.Any] = {}
    fake_redis = LocalFakeRedis(state)

    with (
        patch("app.routers.upload.helpers.MAX_PENDING_UPLOADS", 1),
        patch("app.routers.tus._reserve_storage_limit", new_callable=AsyncMock),
        patch("app.routers.tus.create_multipart_upload", new_callable=AsyncMock) as mock_create,
    ):
        mock_create.return_value = "s3-upload-id"

        from app.core.database.redis import get_redis
        from app.main import app

        async def override_get_redis():
            yield fake_redis

        app.dependency_overrides[get_redis] = override_get_redis

        try:
            headers1 = _auth_headers(user)
            headers1.update(
                {
                    "Tus-Resumable": "1.0.0",
                    "Upload-Length": "1024",
                    "Upload-Metadata": "filename dGVzdC5wZGY=,filetype YXBwbGljYXRpb24vcGRm",
                }
            )
            resp1 = await client.post("/api/upload/tus", headers=headers1)
            assert resp1.status_code == 201

            resp2 = await client.post("/api/upload/tus", headers=headers1)
            assert resp2.status_code == 400
            assert "Too many pending" in resp2.text
        finally:
            app.dependency_overrides.pop(get_redis, None)
