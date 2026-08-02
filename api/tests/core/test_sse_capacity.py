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
    queue = sse.register_topic_queue("material-id", owner_keys=("ip:test",))

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
    queue = sse.register_topic_queue("material-id", owner_keys=("ip:test",))

    queue.put_nowait({"type": "first"})
    sse._deliver_to_topic("material-id", {"type": "overflow"})
    sse._deliver_to_topic("material-id", {"type": "must-be-dropped"})

    assert queue.qsize() == 1
    assert queue.get_nowait()["type"] == "resync_required"


@pytest.mark.asyncio
async def test_consuming_resync_closes_desynchronized_stream(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    queue = sse.register_topic_queue("material-id", owner_keys=("ip:test",))
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

    first = sse.register_topic_queue("one", owner_keys=("ip:test",))

    with pytest.raises(sse.SSECapacityError, match="temporarily at capacity"):
        sse.register_topic_queue("two", owner_keys=("ip:test",))

    sse.unregister_topic_queue("one", first)
    second = sse.register_topic_queue("two", owner_keys=("ip:test",))

    assert second is not first


@pytest.mark.asyncio
async def test_named_stream_preserves_resync_control_event(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    queue = sse.register_topic_queue("notifications", owner_keys=("ip:test",))
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


def test_legacy_single_owner_key_remains_compatible() -> None:
    queue = sse.register_topic_queue("legacy", owner_key="client:legacy")

    assert queue in sse._topic_queues["legacy"]
    sse.unregister_topic_queue("legacy", queue)


def test_topic_owner_connection_limit_and_release(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_TOPIC_CONNECTIONS_PER_OWNER", 2)
    owner_keys = ("client:203.0.113.10",)

    first = sse.register_topic_queue("one", owner_keys=owner_keys)
    second = sse.register_topic_queue("two", owner_keys=owner_keys)

    with pytest.raises(sse.SSECapacityError, match="this client"):
        sse.register_topic_queue("three", owner_keys=owner_keys)

    other = sse.register_topic_queue("three", owner_keys=("client:203.0.113.11",))
    sse.unregister_topic_queue("one", first)
    replacement = sse.register_topic_queue("four", owner_keys=owner_keys)

    assert replacement is not second
    assert other is not replacement


def test_forwarded_hop_limit_cannot_be_bypassed_by_rotating_client_keys(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_TOPIC_CONNECTIONS_PER_FORWARDED_HOP", 2)

    sse.register_topic_queue(
        "one", owner_keys=("client:198.51.100.1", "hop:203.0.113.10", "proxy:172.20.0.10")
    )
    sse.register_topic_queue(
        "two", owner_keys=("client:198.51.100.2", "hop:203.0.113.10", "proxy:172.20.0.10")
    )

    with pytest.raises(sse.SSECapacityError, match="proxy path"):
        sse.register_topic_queue(
            "three",
            owner_keys=("client:198.51.100.3", "hop:203.0.113.10", "proxy:172.20.0.10"),
        )


def test_topic_pool_reserves_capacity_for_user_streams(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_LOCAL_TOPIC_CONNECTIONS", 1)

    topic_queue = sse.register_topic_queue("one", owner_keys=("client:test",))
    with pytest.raises(sse.SSECapacityError, match="Topic live updates"):
        sse.register_topic_queue("two", owner_keys=("client:other",))

    user_id = uuid.uuid4()
    user_queue = sse.register_user_queue(user_id)

    assert user_queue is not topic_queue


def test_topic_owner_keys_prefer_authenticated_identity() -> None:
    user_id = uuid.uuid4()

    assert sse.topic_owner_keys(
        user_id=user_id,
        client_host="172.20.0.10",
        forwarded_for="198.51.100.9",
    ) == (f"user:{user_id}",)


def test_private_proxy_uses_forwarded_chain_and_retains_path_keys() -> None:
    assert sse.topic_owner_keys(
        client_host="172.20.0.10",
        forwarded_for="2001:DB8::1, 203.0.113.8",
        real_ip="203.0.113.8",
    ) == ("client:2001:db8::1", "hop:203.0.113.8", "proxy:172.20.0.10")


def test_single_proxy_real_ip_fallback() -> None:
    assert sse.topic_owner_keys(
        client_host="172.20.0.10",
        real_ip="198.51.100.20",
    ) == ("client:198.51.100.20", "proxy:172.20.0.10")


def test_public_direct_peer_cannot_spoof_forwarded_owner() -> None:
    assert sse.topic_owner_keys(
        client_host="8.8.8.8",
        forwarded_for="203.0.113.99",
        real_ip="203.0.113.99",
    ) == ("client:8.8.8.8",)


def test_forwarded_chain_preserves_both_security_endpoints_when_bounded() -> None:
    chain = ",".join(
        ["198.51.100.1"] + [f"10.0.0.{index}" for index in range(1, 20)] + ["203.0.113.9"]
    )
    parsed = sse._forwarded_ip_chain(chain)

    assert parsed[0] == "198.51.100.1"
    assert parsed[-1] == "203.0.113.9"
    assert len(parsed) <= sse._MAX_FORWARDED_FOR_HOSTS


def test_oversized_forwarded_header_is_ignored_for_safe_real_ip_fallback() -> None:
    oversized = "198.51.100.1," + ("1.1.1.1," * 400) + "203.0.113.9"

    assert sse.topic_owner_keys(
        client_host="172.20.0.10",
        forwarded_for=oversized,
        real_ip="203.0.113.9",
    ) == ("client:203.0.113.9", "proxy:172.20.0.10")


def test_reserved_public_peer_is_not_treated_as_internal_proxy() -> None:
    assert sse.topic_owner_keys(
        client_host="203.0.113.10",
        forwarded_for="198.51.100.99",
    ) == ("client:203.0.113.10",)
