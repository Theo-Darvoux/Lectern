import json
import uuid
from unittest.mock import Mock

import pytest

import app.core.events.sse as sse


@pytest.fixture(autouse=True)
def reset_sse_state():
    sse._user_queues.clear()
    sse._topic_queues.clear()
    sse._topic_queue_owners.clear()
    sse._topic_owner_queue_ids.clear()
    sse._active_topic_queue_ids.clear()
    sse._active_queue_ids.clear()
    sse._desynced_queue_ids.clear()
    yield
    sse._user_queues.clear()
    sse._topic_queues.clear()
    sse._topic_queue_owners.clear()
    sse._topic_owner_queue_ids.clear()
    sse._active_topic_queue_ids.clear()
    sse._active_queue_ids.clear()
    sse._desynced_queue_ids.clear()


def test_topic_overflow_replaces_incremental_events_with_resync(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 2)
    queue = sse.register_topic_queue("material-id", owner_key="ip:test")

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
    queue = sse.register_topic_queue("material-id", owner_key="ip:test")

    queue.put_nowait({"type": "first"})
    sse._deliver_to_topic("material-id", {"type": "overflow"})
    sse._deliver_to_topic("material-id", {"type": "must-be-dropped"})

    assert queue.qsize() == 1
    assert queue.get_nowait()["type"] == "resync_required"


@pytest.mark.asyncio
async def test_consuming_resync_closes_desynchronized_stream(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    queue = sse.register_topic_queue("material-id", owner_key="ip:test")
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

    first = sse.register_topic_queue("one", owner_key="ip:test")

    with pytest.raises(sse.SSECapacityError, match="temporarily at capacity"):
        sse.register_topic_queue("two", owner_key="ip:test")

    sse.unregister_topic_queue("one", first)
    second = sse.register_topic_queue("two", owner_key="ip:test")

    assert second is not first


@pytest.mark.asyncio
async def test_named_stream_preserves_resync_control_event(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    queue = sse.register_topic_queue("notifications", owner_key="ip:test")
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


def test_topic_owner_connection_limit_and_release(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_TOPIC_CONNECTIONS_PER_OWNER", 2)

    first = sse.register_topic_queue("one", owner_key="ip:203.0.113.10")
    second = sse.register_topic_queue("two", owner_key="ip:203.0.113.10")

    with pytest.raises(sse.SSECapacityError, match="this client"):
        sse.register_topic_queue("three", owner_key="ip:203.0.113.10")

    other = sse.register_topic_queue("three", owner_key="ip:203.0.113.11")
    sse.unregister_topic_queue("one", first)
    replacement = sse.register_topic_queue("four", owner_key="ip:203.0.113.10")

    assert replacement is not second
    assert other is not replacement


def test_topic_pool_reserves_capacity_for_user_streams(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_LOCAL_TOPIC_CONNECTIONS", 1)

    topic_queue = sse.register_topic_queue("one", owner_key="ip:test")
    with pytest.raises(sse.SSECapacityError, match="Topic live updates"):
        sse.register_topic_queue("two", owner_key="ip:other")

    user_id = uuid.uuid4()
    user_queue = sse.register_user_queue(user_id)

    assert user_queue is not topic_queue


def test_topic_owner_key_prefers_authenticated_identity() -> None:
    user_id = uuid.uuid4()

    assert sse.topic_owner_key(user_id=user_id, client_host="203.0.113.10") == f"user:{user_id}"
    assert sse.topic_owner_key(client_host=" 2001:DB8::1 ") == "ip:2001:db8::1"
