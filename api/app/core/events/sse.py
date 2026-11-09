"""Live updates: one multiplexed SSE stream per browser.

A stream carries every channel a browser asked for. Delivery is by internal
topic, and each event is wrapped in a ``{channel, type, data}`` envelope. Delivery
is best-effort: a client that may have missed events reconnects and reconciles
from the authoritative HTTP APIs.
"""

import asyncio
import contextlib
import json
import logging
import random
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from app.core.common.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)

_SSE_QUEUE_MAXSIZE = 500
_MAX_LOCAL_SSE_CONNECTIONS = 2_000
# Per process. One browser holds one stream, so this bounds a user's devices.
_MAX_USER_SSE_CONNECTIONS = 8
# Streams outlive the credential check made when they open. Ending them
# periodically makes a revoked session lose access at the next reconnect.
_MAX_STREAM_SECONDS = 3_600.0

_INSTANCE_ID = uuid.uuid4().hex
_FANOUT_CHANNEL = "sse:fanout"

_publish_queue: asyncio.Queue[str] | None = None
_pubsub_tasks: list[asyncio.Task[None]] = []


class SSECapacityError(ServiceUnavailableError):
    """The process cannot safely accept another SSE connection."""

    def __init__(self, detail: str = "Live updates are temporarily at capacity") -> None:
        super().__init__(detail)


@dataclass(eq=False)
class SSEStream:
    """One open browser stream. ``None`` in the queue ends the stream."""

    user_id: uuid.UUID
    client_id: str
    channels_by_topic: dict[str, str]
    started_at: float
    queue: asyncio.Queue[dict[str, Any] | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
    )


_streams: set[SSEStream] = set()
_user_streams: dict[uuid.UUID, set[SSEStream]] = {}
_topic_streams: dict[str, set[SSEStream]] = {}


def notifications_topic(user_id: uuid.UUID | str) -> str:
    return f"notifications:{user_id}"


def uploads_topic(user_id: uuid.UUID | str) -> str:
    return f"uploads:{user_id}"


def register_stream(
    user_id: uuid.UUID,
    client_id: str,
    topic_channels: dict[str, str],
) -> SSEStream:
    """Open a stream, replacing any older one from the same browser.

    ``topic_channels`` maps each public channel to its internal topic. The old
    stream closes only after the new one receives events, so a replacement
    leaves no delivery gap. Dead connections cannot block a reconnect either.
    """
    started_at = time.time()
    _close_client_streams(user_id, client_id, started_at)

    if len(_streams) >= _MAX_LOCAL_SSE_CONNECTIONS:
        raise SSECapacityError()
    if len(_user_streams.get(user_id, ())) >= _MAX_USER_SSE_CONNECTIONS:
        raise SSECapacityError("Too many concurrent live-update connections for this user")

    stream = SSEStream(
        user_id=user_id,
        client_id=client_id,
        channels_by_topic={topic: channel for channel, topic in topic_channels.items()},
        started_at=started_at,
    )
    _streams.add(stream)
    _user_streams.setdefault(user_id, set()).add(stream)
    for topic in stream.channels_by_topic:
        _topic_streams.setdefault(topic, set()).add(stream)

    _publish(
        {"replace": {"user_id": str(user_id), "client_id": client_id, "started_at": started_at}}
    )
    return stream


def unregister_stream(stream: SSEStream) -> None:
    """Remove a stream from every index. Safe to call more than once."""
    _streams.discard(stream)
    _discard_from_index(_user_streams, stream.user_id, stream)
    for topic in stream.channels_by_topic:
        _discard_from_index(_topic_streams, topic, stream)


def _discard_from_index[K](index: dict[K, set[SSEStream]], key: K, stream: SSEStream) -> None:
    streams = index.get(key)
    if streams is None:
        return
    streams.discard(stream)
    if not streams:
        del index[key]


def _close_stream(stream: SSEStream) -> None:
    """Stop delivering to a stream and end it once its queue is consumed."""
    unregister_stream(stream)
    while True:
        try:
            stream.queue.put_nowait(None)
            return
        except asyncio.QueueFull:
            stream.queue.get_nowait()


def _close_client_streams(user_id: uuid.UUID, client_id: str, started_before: float) -> None:
    for stream in list(_user_streams.get(user_id, ())):
        if stream.client_id == client_id and stream.started_at <= started_before:
            _close_stream(stream)


def _deliver_to_topic(topic: str, event: dict[str, Any]) -> None:
    for stream in list(_topic_streams.get(topic, ())):
        envelope = {
            "channel": stream.channels_by_topic[topic],
            "type": str(event.get("type") or "message"),
            "data": event,
        }
        try:
            stream.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            # Ending the stream makes the client reconnect and reconcile.
            logger.warning("SSE queue overflow for topic %s; closing stream", topic)
            _close_stream(stream)


def broadcast_to_topic(topic: str, event: dict[str, Any]) -> None:
    """Broadcast an event to all watchers of a topic, across processes."""
    _deliver_to_topic(topic, event)
    _publish({"topic": topic, "event": event})


def broadcast_to_user(user_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Broadcast a notification to all of a user's streams, across processes."""
    broadcast_to_topic(notifications_topic(user_id), event)


# --- Redis pub/sub bridge ----------------------------------------------------


def _publish(message: dict[str, Any]) -> None:
    """Queue a message for fan-out to other processes (best-effort)."""
    if _publish_queue is None:
        return

    try:
        payload = json.dumps({"origin": _INSTANCE_ID, **message}, default=str)
        _publish_queue.put_nowait(payload)
    except TypeError:
        logger.error("Failed to serialize SSE fan-out message")
    except asyncio.QueueFull:
        logger.warning("SSE fan-out queue full; dropping message")


async def _publisher_loop() -> None:
    from app.core.database.redis import redis_client

    assert _publish_queue is not None
    while True:
        payload = await _publish_queue.get()
        try:
            await redis_client.publish(_FANOUT_CHANNEL, payload)
        except Exception:
            logger.exception("Failed to publish SSE fan-out message")
        finally:
            _publish_queue.task_done()


async def _subscriber_loop() -> None:
    from app.core.database.redis import redis_client

    reconnecting = False
    while True:
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(_FANOUT_CHANNEL)
            if reconnecting:
                # Events published during the outage are gone. Ending local
                # streams makes their clients reconnect and reconcile.
                for stream in list(_streams):
                    _close_stream(stream)
                reconnecting = False
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                _handle_fanout_message(message["data"])
        except asyncio.CancelledError:
            await pubsub.aclose()  # type: ignore[attr-defined]
            raise
        except Exception:
            logger.exception("SSE fan-out subscriber error; reconnecting in 2s")
            reconnecting = True
            with contextlib.suppress(Exception):
                await pubsub.aclose()  # type: ignore[attr-defined]
            await asyncio.sleep(2)


def _handle_fanout_message(raw: Any) -> None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Malformed SSE fan-out message")
        return
    if not isinstance(data, dict) or data.get("origin") == _INSTANCE_ID:
        return

    replace = data.get("replace")
    if isinstance(replace, dict):
        try:
            _close_client_streams(
                uuid.UUID(replace["user_id"]),
                str(replace["client_id"]),
                float(replace["started_at"]),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Invalid stream replacement in SSE fan-out message")
        return

    topic = data.get("topic")
    event = data.get("event")
    if isinstance(topic, str) and isinstance(event, dict):
        _deliver_to_topic(topic, event)


async def start_sse_pubsub(subscribe: bool = True) -> None:
    """Start the Redis pub/sub fan-out bridge."""
    global _publish_queue
    if _pubsub_tasks:
        return

    _publish_queue = asyncio.Queue(maxsize=2000)

    _pubsub_tasks.append(asyncio.create_task(_publisher_loop()))
    if subscribe:
        _pubsub_tasks.append(asyncio.create_task(_subscriber_loop()))


async def stop_sse_pubsub() -> None:
    global _publish_queue
    for task in _pubsub_tasks:
        task.cancel()
    for task in _pubsub_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _pubsub_tasks.clear()
    _publish_queue = None


# --- Stream body -------------------------------------------------------------


async def stream_events(stream: SSEStream) -> AsyncGenerator[dict[str, str], None]:
    """Yield queued envelopes until the stream is closed or reaches its lifetime."""
    # Jittered so streams opened together do not all reconnect together.
    lifetime = _MAX_STREAM_SECONDS * random.uniform(0.8, 1.0)  # noqa: S311
    deadline = asyncio.get_running_loop().time() + lifetime
    while True:
        try:
            # Scoped to the get(): a timeout must never span a generator yield.
            async with asyncio.timeout_at(deadline):
                event = await stream.queue.get()
        except TimeoutError:
            return
        if event is None:
            return
        yield {"data": json.dumps(event, default=str)}
