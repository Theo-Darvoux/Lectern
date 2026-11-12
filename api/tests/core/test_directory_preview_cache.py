import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.database.content_generation import bump_content_generation
from app.services.directory import get_preview_material_ids


class _FakePipeline:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store
        self._ops: list[tuple[str, Any]] = []

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self._ops.append((key, value))

    async def execute(self) -> None:
        for key, value in self._ops:
            self._store[key] = value.encode() if isinstance(value, str) else value


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def mget(self, keys: list[str]) -> list[Any]:
        return [self.store.get(key) for key in keys]

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, b"0")) + 1
        self.store[key] = str(value).encode()
        return value

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self.store)


@pytest.mark.asyncio
async def test_preview_ids_are_cached_until_the_content_generation_moves() -> None:
    root_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    redis = _FakeRedis()
    db = AsyncMock()
    db.execute.return_value = [(root_id, first)]

    assert await get_preview_material_ids(db, [root_id], redis) == {root_id: [str(first)]}
    assert db.execute.await_count == 1

    # A second listing is served from the cache.
    assert await get_preview_material_ids(db, [root_id], redis) == {root_id: [str(first)]}
    assert db.execute.await_count == 1

    # A committed content change orphans every cached entry.
    await bump_content_generation(redis)
    db.execute.return_value = [(root_id, second)]
    assert await get_preview_material_ids(db, [root_id], redis) == {root_id: [str(second)]}
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_preview_ids_fall_back_to_the_query_when_redis_fails() -> None:
    root_id = uuid.uuid4()
    material_id = uuid.uuid4()
    redis = AsyncMock()
    redis.get.side_effect = ConnectionError("down")
    db = AsyncMock()
    db.execute.return_value = [(root_id, material_id)]

    assert await get_preview_material_ids(db, [root_id], redis) == {root_id: [str(material_id)]}
