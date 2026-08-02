import json
import uuid
from unittest.mock import Mock

import pytest

import app.core.events.sse as sse


@pytest.fixture(autouse=True)
def reset_sse_state():
    sse._user_queues.clear()
    sse._topic_queues.clear()
    sse._active_queue_ids.clear()
    sse._desynced_queue_ids.clear()
    yield
    sse._user_queues.clear()
    sse._topic_queues.clear()
    sse._active_queue_ids.clear()
    sse._desynced_queue_ids.clear()


def test_topic_overflow_replaces_incremental_events_with_resync(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 2)
    queue = sse.register_topic_queue("material-id")

    queue.put_nowait({"type": "one"})
    queue.put_nowait({"type": "two"})

    sse._deliver_to_topic("material-id", {"type": "three"})

    assert queue.qsize() == 1
    assert queue.get_nowait() == {
        "type": "resync_required",
        "reason": "event_buffer_overflow",
    }


def test_desynced_queue_drops_later_incremental_events(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    queue = sse.register_topic_queue("material-id")

    queue.put_nowait({"type": "first"})
    sse._deliver_to_topic("material-id", {"type": "overflow"})
    sse._deliver_to_topic("material-id", {"type": "must-be-dropped"})

    assert queue.qsize() == 1
    assert queue.get_nowait()["type"] == "resync_required"


@pytest.mark.asyncio
async def test_consuming_resync_closes_desynchronized_stream(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    queue = sse.register_topic_queue("material-id")
    cleanup = Mock()

    queue.put_nowait({"type": "first"})
    sse._deliver_to_topic("material-id", {"type": "overflow"})

    stream = sse.sse_event_stream(queue, cleanup, keepalive_seconds=1)
    delivered = await anext(stream)

    assert delivered["event"] == "resync_required"
    assert json.loads(delivered["data"])["reason"] == "event_buffer_overflow"

    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    cleanup.assert_called_once()


def test_per_user_connection_limit(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_USER_SSE_CONNECTIONS", 2)
    user_id = uuid.uuid4()

    first = sse.register_user_queue(user_id)
    second = sse.register_user_queue(user_id)

    with pytest.raises(sse.SSECapacityError, match="Too many concurrent"):
        sse.register_user_queue(user_id)

    sse.unregister_user_queue(user_id, first)
    replacement = sse.register_user_queue(user_id)

    assert replacement is not second


def test_process_connection_limit(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_LOCAL_SSE_CONNECTIONS", 1)

    first = sse.register_topic_queue("one")

    with pytest.raises(sse.SSECapacityError, match="temporarily at capacity"):
        sse.register_topic_queue("two")

    sse.unregister_topic_queue("one", first)
    second = sse.register_topic_queue("two")

    assert second is not first

@pytest.mark.asyncio
async def test_named_stream_preserves_resync_control_event(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    queue = sse.register_topic_queue("notifications")
    cleanup = Mock()

    queue.put_nowait({"type": "notification"})
    sse._deliver_to_topic("notifications", {"type": "overflow"})

    stream = sse.sse_event_stream(
        queue,
        cleanup,
        event_name="notification",
        keepalive_seconds=1,
    )
    delivered = await anext(stream)

    assert delivered["event"] == "resync_required"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    cleanup.assert_called_once()
