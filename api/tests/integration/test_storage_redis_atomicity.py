from __future__ import annotations

import asyncio
import os

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security.cas import (
    _STORAGE_USAGE_GENERATION_KEY,
    _STORAGE_USAGE_KEY,
    increment_cas_ref,
)
from app.core.storage import capacity

pytestmark = pytest.mark.integration

_REDIS_URL = os.environ.get("AUTH_ATOMICITY_REDIS_URL")


@pytest.fixture
async def redis() -> Redis:  # type: ignore[type-arg]
    if not _REDIS_URL:
        pytest.skip("AUTH_ATOMICITY_REDIS_URL is required for this integration test")
    client = Redis.from_url(_REDIS_URL, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


async def test_cas_creation_bumps_physical_usage_generation_with_real_redis(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    sha256 = "d" * 64
    await increment_cas_ref(
        redis,
        sha256,
        initial_data={
            "final_key": "cas/real-redis-generation",
            "size": 17,
            "mime_type": "text/plain",
            "file_name": "generation.txt",
        },
        operation_id="integration:cas-generation:create",
    )

    assert int(await redis.get(_STORAGE_USAGE_KEY) or 0) == 17
    assert int(await redis.get(_STORAGE_USAGE_GENERATION_KEY) or 0) == 1


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
    await snapshot_read.wait()
    await capacity.release_promoted_legacy_storage_reservation("promoted", redis)
    allow_first_attempt.set()
    await reservation

    assert calls >= 2
    assert int(await redis.get(capacity.LEGACY_STORAGE_GENERATION_KEY) or 0) == 1
