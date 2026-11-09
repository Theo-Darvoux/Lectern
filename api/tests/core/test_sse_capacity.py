import json
import uuid

import pytest

import app.core.events.sse as sse


@pytest.fixture(autouse=True)
def reset_sse_state():
    for registry in (sse._streams, sse._user_streams, sse._topic_streams):
        registry.clear()
    yield
    for registry in (sse._streams, sse._user_streams, sse._topic_streams):
        registry.clear()


def _register(
    user_id: uuid.UUID | None = None,
    client: str = "browser-a",
    topics: dict[str, str] | None = None,
) -> sse.SSEStream:
    return sse.register_stream(user_id or uuid.uuid4(), client, topics or {})


def test_stream_routes_each_topic_to_its_public_channel() -> None:
    user_id = uuid.uuid4()
    stream = _register(
        user_id,
        topics={
            "notifications": sse.notifications_topic(user_id),
            "material:material-id": "material-id",
        },
    )

    sse.broadcast_to_user(user_id, {"type": "notification", "id": "n1"})
    sse._deliver_to_topic("material-id", {"type": "material_updated", "id": "v1"})

    assert stream.queue.get_nowait() == {
        "channel": "notifications",
        "type": "notification",
        "data": {"type": "notification", "id": "n1"},
    }
    assert stream.queue.get_nowait() == {
        "channel": "material:material-id",
        "type": "material_updated",
        "data": {"type": "material_updated", "id": "v1"},
    }


def test_uploads_are_delivered_only_to_the_owner() -> None:
    owner, other = uuid.uuid4(), uuid.uuid4()
    owner_stream = _register(owner, topics={"uploads": sse.uploads_topic(owner)})
    other_stream = _register(other, topics={"uploads": sse.uploads_topic(other)})

    sse.broadcast_to_topic(sse.uploads_topic(owner), {"type": "upload", "file_key": "k"})

    assert owner_stream.queue.get_nowait()["channel"] == "uploads"
    assert other_stream.queue.empty()


def test_same_browser_replaces_its_stream_and_old_stream_ends() -> None:
    user_id = uuid.uuid4()
    old = _register(user_id, "browser-a", {"material:m": "m"})
    new = _register(user_id, "browser-a", {"material:m": "m"})

    assert sse._user_streams[user_id] == {new}
    assert sse._topic_streams["m"] == {new}
    assert old.queue.get_nowait() is None

    sse._deliver_to_topic("m", {"type": "material_updated"})
    assert old.queue.empty()
    assert new.queue.get_nowait()["type"] == "material_updated"


def test_replacement_keeps_events_queued_before_the_close() -> None:
    user_id = uuid.uuid4()
    old = _register(user_id, "browser-a", {"material:m": "m"})
    sse._deliver_to_topic("m", {"type": "material_updated"})

    _register(user_id, "browser-a", {"material:m": "m"})

    assert old.queue.get_nowait()["type"] == "material_updated"
    assert old.queue.get_nowait() is None


def test_each_browser_of_a_user_keeps_its_own_stream() -> None:
    user_id = uuid.uuid4()
    laptop = _register(user_id, "laptop")
    phone = _register(user_id, "phone")

    assert sse._user_streams[user_id] == {laptop, phone}
    assert laptop.queue.empty()


def test_per_user_stream_limit_counts_distinct_browsers(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_USER_SSE_CONNECTIONS", 2)
    user_id = uuid.uuid4()
    first = _register(user_id, "one")
    _register(user_id, "two")

    with pytest.raises(sse.SSECapacityError, match="Too many concurrent"):
        _register(user_id, "three")

    # Replacing a browser's own stream never counts against the limit.
    _register(user_id, "two")

    sse.unregister_stream(first)
    assert _register(user_id, "three") is not first


def test_process_stream_limit(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_LOCAL_SSE_CONNECTIONS", 1)
    first = _register(client="one")

    with pytest.raises(sse.SSECapacityError, match="at capacity"):
        _register(client="two")

    sse.unregister_stream(first)
    assert _register(client="two") is not first


def test_unregister_is_idempotent_and_clears_every_index() -> None:
    user_id = uuid.uuid4()
    stream = _register(user_id, topics={"material:m": "m"})

    sse.unregister_stream(stream)
    sse.unregister_stream(stream)

    assert not sse._streams
    assert user_id not in sse._user_streams
    assert "m" not in sse._topic_streams


def test_queue_overflow_ends_stream_for_client_reconciliation(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    user_id = uuid.uuid4()
    stream = _register(user_id, topics={"material:m": "m"})

    sse._deliver_to_topic("m", {"type": "first"})
    sse._deliver_to_topic("m", {"type": "overflow"})

    assert stream.queue.get_nowait() is None
    assert not sse._streams


@pytest.mark.asyncio
async def test_stream_events_yields_envelopes_until_closed() -> None:
    stream = _register(topics={"material:m": "m"})
    sse._deliver_to_topic("m", {"type": "material_updated", "id": "v1"})
    sse._close_stream(stream)

    events = [event async for event in sse.stream_events(stream)]

    assert len(events) == 1
    assert "event" not in events[0]
    assert json.loads(events[0]["data"]) == {
        "channel": "material:m",
        "type": "material_updated",
        "data": {"type": "material_updated", "id": "v1"},
    }


@pytest.mark.asyncio
async def test_stream_ends_at_its_maximum_lifetime(monkeypatch) -> None:
    monkeypatch.setattr(sse, "_MAX_STREAM_SECONDS", 0.01)
    stream = _register()

    assert [event async for event in sse.stream_events(stream)] == []


def test_fanout_replace_closes_older_stream_of_same_browser_only() -> None:
    user_id = uuid.uuid4()
    same_browser = _register(user_id, "browser-a")
    other_browser = _register(user_id, "browser-b")

    sse._handle_fanout_message(
        json.dumps(
            {
                "origin": "another-process",
                "replace": {
                    "user_id": str(user_id),
                    "client_id": "browser-a",
                    "started_at": same_browser.started_at + 1,
                },
            }
        )
    )

    assert sse._user_streams[user_id] == {other_browser}
    assert same_browser.queue.get_nowait() is None
    assert other_browser.queue.empty()


def test_fanout_replace_cannot_close_a_newer_stream() -> None:
    user_id = uuid.uuid4()
    stream = _register(user_id, "browser-a")

    sse._handle_fanout_message(
        json.dumps(
            {
                "origin": "another-process",
                "replace": {
                    "user_id": str(user_id),
                    "client_id": "browser-a",
                    "started_at": stream.started_at - 1,
                },
            }
        )
    )

    assert sse._user_streams[user_id] == {stream}


def test_fanout_delivers_remote_topic_events_and_ignores_own_messages() -> None:
    stream = _register(topics={"material:m": "m"})
    event = {"type": "material_updated"}

    sse._handle_fanout_message(
        json.dumps({"origin": sse._INSTANCE_ID, "topic": "m", "event": event})
    )
    assert stream.queue.empty()

    sse._handle_fanout_message(json.dumps({"origin": "remote", "topic": "m", "event": event}))
    assert stream.queue.get_nowait()["type"] == "material_updated"


@pytest.mark.parametrize("raw", ["not json", "[]", '{"origin": "r", "replace": {"user_id": "x"}}'])
def test_fanout_ignores_malformed_messages(raw: str) -> None:
    sse._handle_fanout_message(raw)
