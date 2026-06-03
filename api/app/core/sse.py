import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

logger = logging.getLogger(__name__)

_user_queues: dict[uuid.UUID, list[asyncio.Queue[dict[str, Any]]]] = {}
_topic_queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

# --- Cross-process fan-out ---------------------------------------------------
#
# SSE client connections live in whichever API process accepted them, but events
# are produced all over the place: other API replicas, and the ARQ worker
# processes (e.g. auto-merge of a PR emits ``pr_approved``). A purely in-process
# queue would silently drop those. To bridge processes we publish every
# broadcast onto a single Redis pub/sub channel; every API process subscribes
# and re-delivers to its own local queues.
#
# Each process tags its publishes with a unique ``_INSTANCE_ID`` and delivers to
# its own local queues immediately (so same-process delivery stays fast and
# keeps working even if Redis pub/sub is momentarily unavailable). The
# subscriber skips messages it published itself to avoid double delivery.

_INSTANCE_ID = uuid.uuid4().hex
_FANOUT_CHANNEL = "sse:fanout"

_publish_queue: asyncio.Queue[str] | None = None
_pubsub_tasks: list[asyncio.Task[None]] = []


# --- User-keyed queues (1:N mapping, used for notifications) ---


def register_user_queue(user_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
    """Register an SSE queue for a user. Supports multiple concurrent connections."""
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _user_queues.setdefault(user_id, []).append(q)
    return q


def unregister_user_queue(user_id: uuid.UUID, q: asyncio.Queue[dict[str, Any]]) -> None:
    """Unregister a specific SSE queue for a user."""
    queues = _user_queues.get(user_id, [])
    with contextlib.suppress(ValueError):
        queues.remove(q)
    if not queues:
        _user_queues.pop(user_id, None)


def _deliver_to_user(user_id: uuid.UUID, event: dict[str, Any]) -> None:
    for q in list(_user_queues.get(user_id, [])):
        q.put_nowait(event)


def broadcast_to_user(user_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Broadcast an event to all active SSE connections for a user, across processes."""
    _deliver_to_user(user_id, event)
    _publish("user", str(user_id), event)


# --- Topic-keyed queues (1:N mapping, used for material annotations) ---


def register_topic_queue(topic: str) -> asyncio.Queue[dict[str, Any]]:
    """Register a watcher queue for a topic (e.g. a material_id)."""
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _topic_queues.setdefault(topic, []).append(q)
    return q


def unregister_topic_queue(topic: str, q: asyncio.Queue[dict[str, Any]]) -> None:
    queues = _topic_queues.get(topic, [])
    with contextlib.suppress(ValueError):
        queues.remove(q)
    if not queues:
        _topic_queues.pop(topic, None)


def _deliver_to_topic(topic: str, event: dict[str, Any]) -> None:
    for q in list(_topic_queues.get(topic, [])):
        q.put_nowait(event)


def broadcast_to_topic(topic: str, event: dict[str, Any]) -> None:
    """Broadcast an event to all watchers of a topic, across processes."""
    _deliver_to_topic(topic, event)
    _publish("topic", topic, event)


# --- Redis pub/sub bridge ----------------------------------------------------


def _publish(scope: str, key: str, event: dict[str, Any]) -> None:
    """Queue a broadcast for fan-out to other processes (best-effort)."""
    if _publish_queue is None:
        return
    payload = json.dumps({"origin": _INSTANCE_ID, "scope": scope, "key": key, "event": event})
    try:
        _publish_queue.put_nowait(payload)
    except asyncio.QueueFull:  # pragma: no cover - unbounded queue, defensive only
        logger.warning("SSE fan-out queue full; dropping %s broadcast", scope)


async def _publisher_loop() -> None:
    from app.core.redis import redis_client

    assert _publish_queue is not None
    while True:
        payload = await _publish_queue.get()
        try:
            await redis_client.publish(_FANOUT_CHANNEL, payload)
        except Exception:
            logger.exception("Failed to publish SSE fan-out message")


async def _subscriber_loop() -> None:
    from app.core.redis import redis_client

    while True:
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(_FANOUT_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                _handle_fanout_message(message["data"])
        except asyncio.CancelledError:
            await pubsub.aclose()  # type: ignore[attr-defined]
            raise
        except Exception:
            logger.exception("SSE fan-out subscriber error; reconnecting in 2s")
            with contextlib.suppress(Exception):
                await pubsub.aclose()  # type: ignore[attr-defined]
            await asyncio.sleep(2)


def _handle_fanout_message(raw: Any) -> None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Malformed SSE fan-out message")
        return
    if data.get("origin") == _INSTANCE_ID:
        # We already delivered this locally when it was produced.
        return
    scope = data.get("scope")
    key = data.get("key")
    event = data.get("event")
    if not isinstance(event, dict) or not key:
        return
    if scope == "user":
        try:
            _deliver_to_user(uuid.UUID(key), event)
        except ValueError:
            logger.warning("Invalid user id in SSE fan-out message")
    elif scope == "topic":
        _deliver_to_topic(key, event)


async def start_sse_pubsub(subscribe: bool = True) -> None:
    """Start the Redis pub/sub fan-out bridge.

    Args:
        subscribe: If True (API processes), also listen for events produced by
            other processes and deliver them to local SSE queues. Worker
            processes only produce events, so they pass ``subscribe=False`` to
            avoid an idle subscriber connection.
    """
    global _publish_queue
    if _pubsub_tasks:
        return
    _publish_queue = asyncio.Queue()
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


# --- Reusable SSE event generator ---


async def sse_event_stream(
    queue: asyncio.Queue[dict[str, Any]],
    cleanup: Callable[[], None],
    event_name: str | None = None,
    keepalive_seconds: float = 30.0,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Generic SSE event generator.

    Args:
        queue: The asyncio.Queue to read events from.
        cleanup: Called in ``finally`` to unregister the queue.
        event_name: If set, all events use this fixed name.
                    If None, the name is read from ``event["type"]``.
        keepalive_seconds: Interval for keepalive pings.
    """
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                if event.get("type") == "close":
                    break
                yield {
                    "event": event_name or event.get("type", "message"),
                    "data": json.dumps(event),
                }
            except TimeoutError:
                yield {"event": "ping", "data": ""}
    finally:
        cleanup()
