import asyncio
from unittest.mock import AsyncMock

import pytest

from app.routers.upload.sse import (
    _enqueue_pubsub_payload,
    _load_event_log,
)


@pytest.mark.asyncio
async def test_event_log_replay_reads_from_last_event_offset() -> None:
    redis = AsyncMock()
    redis.lrange.return_value = [b'{"status":"processing"}', '{"status":"clean"}']

    events = await _load_event_log(
        redis,
        "upload:eventlog:key",
        start=1,
    )

    redis.lrange.assert_awaited_once_with("upload:eventlog:key", 1, -1)
    assert events == ['{"status":"processing"}', '{"status":"clean"}']


@pytest.mark.asyncio
async def test_event_log_replay_clamps_negative_offset() -> None:
    redis = AsyncMock()
    redis.lrange.return_value = []

    await _load_event_log(redis, "upload:eventlog:key", start=-10)

    redis.lrange.assert_awaited_once_with("upload:eventlog:key", 0, -1)


def test_handoff_overflow_closes_stream_instead_of_growing_memory() -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
    queue.put_nowait("old")

    accepted = _enqueue_pubsub_payload(queue, "new")

    assert accepted is False
    assert queue.qsize() == 1
    assert queue.get_nowait() is None
