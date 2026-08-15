import asyncio
import contextlib
import ipaddress
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

from app.core.common.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)

_SSE_QUEUE_MAXSIZE = 500
_MAX_LOCAL_SSE_CONNECTIONS = 2_000
_MAX_LOCAL_TOPIC_CONNECTIONS = 1_500
_MAX_USER_SSE_CONNECTIONS = 1
_MAX_TOPIC_CONNECTIONS_PER_OWNER = 20
_MAX_TOPIC_CONNECTIONS_PER_FORWARDED_HOP = 200
_MAX_TOPIC_CONNECTIONS_PER_PROXY_PEER = 1_200
_MAX_FORWARDED_FOR_HOSTS = 16
_MAX_FORWARDED_FOR_CHARS = 2_048
_INTERNAL_PROXY_NETWORKS_V4: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)
_INTERNAL_PROXY_NETWORKS_V6: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("fc00::/7"),
)
_RESYNC_EVENT: dict[str, Any] = {
    "type": "resync_required",
    "reason": "event_buffer_overflow",
}

_user_queues: dict[uuid.UUID, list[asyncio.Queue[dict[str, Any]]]] = {}
_topic_queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
_topic_queue_owners: dict[int, tuple[str, ...]] = {}
_topic_owner_queue_ids: dict[str, set[int]] = {}
_active_topic_queue_ids: set[int] = set()
_active_queue_ids: set[int] = set()
_desynced_queue_ids: set[int] = set()
_master_topic_channels: dict[int, dict[str, tuple[str, ...]]] = {}

_INSTANCE_ID = uuid.uuid4().hex
_FANOUT_CHANNEL = "sse:fanout"

_publish_queue: asyncio.Queue[str] | None = None
_pubsub_tasks: list[asyncio.Task[None]] = []


class SSECapacityError(ServiceUnavailableError):
    """The process cannot safely accept another SSE connection."""

    def __init__(self, detail: str = "Live updates are temporarily at capacity") -> None:
        super().__init__(detail)


def _new_queue() -> asyncio.Queue[dict[str, Any]]:
    if len(_active_queue_ids) >= _MAX_LOCAL_SSE_CONNECTIONS:
        raise SSECapacityError()

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
    _active_queue_ids.add(id(queue))
    return queue


def _release_queue(queue: asyncio.Queue[dict[str, Any]]) -> None:
    queue_id = id(queue)
    _active_queue_ids.discard(queue_id)
    _desynced_queue_ids.discard(queue_id)


def _enqueue_or_request_resync(
    queue: asyncio.Queue[dict[str, Any]],
    event: dict[str, Any],
) -> bool:
    """Enqueue an event, replacing an overflowed stream with one resync marker.

    Returns ``True`` when the queue overflowed. While a resync marker is pending,
    later incremental events are dropped because their ordering is no longer
    trustworthy.
    """
    queue_id = id(queue)
    if queue_id in _desynced_queue_ids:
        return False

    try:
        queue.put_nowait(event)
        return False
    except asyncio.QueueFull:
        pass

    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    queue.put_nowait(dict(_RESYNC_EVENT))
    _desynced_queue_ids.add(queue_id)
    return True


# --- User-keyed queues (1:N mapping, used for notifications) ---


def register_user_queue(user_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
    """Register a bounded SSE queue for a user."""
    queues = _user_queues.get(user_id, [])
    if len(queues) >= _MAX_USER_SSE_CONNECTIONS:
        raise SSECapacityError("Too many concurrent live-update connections for this user")

    queue = _new_queue()
    _user_queues.setdefault(user_id, []).append(queue)
    return queue


def unregister_user_queue(user_id: uuid.UUID, queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Unregister a specific SSE queue for a user."""
    queues = _user_queues.get(user_id, [])
    with contextlib.suppress(ValueError):
        queues.remove(queue)
    _release_queue(queue)
    if not queues:
        _user_queues.pop(user_id, None)


def _deliver_to_user(user_id: uuid.UUID, event: dict[str, Any]) -> None:
    for queue in list(_user_queues.get(user_id, [])):
        outgoing = event
        if id(queue) in _master_topic_channels:
            outgoing = {
                "type": str(event.get("type") or "message"),
                "channel": "notifications",
                "data": event,
            }
        if _enqueue_or_request_resync(queue, outgoing):
            logger.warning("SSE user queue overflow for user %s; requesting resync", user_id)


def broadcast_to_user(user_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Broadcast an event to all active SSE connections for a user, across processes."""
    _deliver_to_user(user_id, event)
    _publish("user", str(user_id), event)


# --- Topic-keyed queues (1:N mapping, used for material annotations) ---


def register_master_queue(
    user_id: uuid.UUID,
    topic_channels: dict[str, str],
) -> asyncio.Queue[dict[str, Any]]:
    """Register one physical user stream for user and topic event sources.

    ``topic_channels`` maps the public logical channel used by the browser to
    the existing internal topic key used by publishers. One bounded queue and
    one capacity slot back the entire multiplexed stream.
    """
    queues = _user_queues.get(user_id, [])
    if len(queues) >= _MAX_USER_SSE_CONNECTIONS:
        raise SSECapacityError("Too many concurrent live-update connections for this user")
    if len(_active_topic_queue_ids) >= _MAX_LOCAL_TOPIC_CONNECTIONS:
        raise SSECapacityError("Topic live updates are temporarily at capacity")

    internal_to_channels: dict[str, list[str]] = {}
    for channel, topic in topic_channels.items():
        normalized_channel = channel.strip()
        normalized_topic = topic.strip()
        if not normalized_channel or not normalized_topic:
            raise ValueError("Master SSE topic channels must not be empty")
        internal_to_channels.setdefault(normalized_topic, []).append(normalized_channel)

    queue = _new_queue()
    queue_id = id(queue)
    owner_key = f"user:{user_id}"
    _user_queues.setdefault(user_id, []).append(queue)
    for topic in internal_to_channels:
        _topic_queues.setdefault(topic, []).append(queue)
    _master_topic_channels[queue_id] = {
        topic: tuple(channels) for topic, channels in internal_to_channels.items()
    }
    _topic_queue_owners[queue_id] = (owner_key,)
    _topic_owner_queue_ids.setdefault(owner_key, set()).add(queue_id)
    _active_topic_queue_ids.add(queue_id)
    return queue


def unregister_master_queue(
    user_id: uuid.UUID,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    """Atomically release every source registration for a master stream."""
    queue_id = id(queue)
    channels = _master_topic_channels.pop(queue_id, None)
    if channels is None:
        return

    user_queues = _user_queues.get(user_id, [])
    with contextlib.suppress(ValueError):
        user_queues.remove(queue)
    if not user_queues:
        _user_queues.pop(user_id, None)

    for topic in channels:
        topic_queues = _topic_queues.get(topic, [])
        with contextlib.suppress(ValueError):
            topic_queues.remove(queue)
        if not topic_queues:
            _topic_queues.pop(topic, None)

    for owner_key in _topic_queue_owners.pop(queue_id, ()):
        owner_queue_ids = _topic_owner_queue_ids.get(owner_key)
        if owner_queue_ids is not None:
            owner_queue_ids.discard(queue_id)
            if not owner_queue_ids:
                _topic_owner_queue_ids.pop(owner_key, None)
    _active_topic_queue_ids.discard(queue_id)
    _release_queue(queue)


def _normalize_owner_host(
    host: str | None,
) -> tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None]:
    normalized = (host or "unknown").strip().casefold()
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized[:255] or "unknown", None
    return str(address), address


def _is_internal_proxy_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if address.is_loopback or address.is_link_local:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in _INTERNAL_PROXY_NETWORKS_V4)
    return any(address in network for network in _INTERNAL_PROXY_NETWORKS_V6)


def _forwarded_ip_chain(value: str | None) -> tuple[str, ...]:
    if not value or len(value) > _MAX_FORWARDED_FOR_CHARS:
        return ()

    raw_hosts = value.split(",")
    if len(raw_hosts) > _MAX_FORWARDED_FOR_HOSTS:
        half = _MAX_FORWARDED_FOR_HOSTS // 2
        raw_hosts = raw_hosts[:half] + raw_hosts[-half:]

    hosts: list[str] = []
    for raw_host in raw_hosts:
        normalized, address = _normalize_owner_host(raw_host)
        if address is not None:
            hosts.append(normalized)
    return tuple(hosts)


def topic_owner_keys(
    *,
    user_id: uuid.UUID | str | None = None,
    client_host: str | None = None,
    forwarded_for: str | None = None,
    real_ip: str | None = None,
) -> tuple[str, ...]:
    """Build hierarchical capacity keys for one topic-stream client.

    Authenticated users are keyed by user ID. Anonymous streams use the ASGI
    client directly unless it is a private/loopback/link-local proxy peer. For
    the checked-in Nginx topology, the leftmost valid ``X-Forwarded-For`` value
    identifies the external client and the rightmost value identifies the hop
    that connected to Nginx. ``X-Real-IP`` is a fallback for single-proxy
    deployments.

    Every derived layer is retained as a quota key. A directly connected private
    client can forge forwarded headers, but rotating the apparent client cannot
    evade the bounded forwarded-hop and immediate-proxy pools.
    """
    if user_id is not None:
        return (f"user:{user_id}",)

    proxy_host, proxy_address = _normalize_owner_host(client_host)
    client_identity = proxy_host
    forwarded_hop: str | None = None

    if proxy_address is not None and _is_internal_proxy_address(proxy_address):
        chain = _forwarded_ip_chain(forwarded_for)
        if chain:
            client_identity = chain[0]
            forwarded_hop = chain[-1]
        else:
            real_identity, real_address = _normalize_owner_host(real_ip)
            if real_address is not None:
                client_identity = real_identity
                forwarded_hop = real_identity

    keys = [f"client:{client_identity}"]
    if forwarded_hop is not None and forwarded_hop != client_identity:
        keys.append(f"hop:{forwarded_hop}")
    if proxy_host not in {client_identity, forwarded_hop}:
        keys.append(f"proxy:{proxy_host}")
    return tuple(keys)


def topic_owner_key(
    *,
    user_id: uuid.UUID | str | None = None,
    client_host: str | None = None,
) -> str:
    """Backward-compatible primary owner key helper."""
    return topic_owner_keys(user_id=user_id, client_host=client_host)[0]


def _topic_owner_limit(owner_key: str) -> int:
    if owner_key.startswith("hop:"):
        return _MAX_TOPIC_CONNECTIONS_PER_FORWARDED_HOP
    if owner_key.startswith("proxy:"):
        return _MAX_TOPIC_CONNECTIONS_PER_PROXY_PEER
    return _MAX_TOPIC_CONNECTIONS_PER_OWNER


def register_topic_queue(
    topic: str,
    *,
    owner_keys: tuple[str, ...] | None = None,
    owner_key: str | None = None,
) -> asyncio.Queue[dict[str, Any]]:
    """Register a bounded topic queue with hierarchical owner limits.

    ``owner_key`` remains accepted for compatibility with older internal callers;
    new request paths should supply every derived layer through ``owner_keys``.
    """
    if owner_keys is not None and owner_key is not None:
        raise ValueError("Supply either topic SSE owner_keys or owner_key, not both")
    raw_owner_keys = owner_keys if owner_keys is not None else ((owner_key,) if owner_key else ())
    normalized_owners = tuple(
        dict.fromkeys(owner.strip() for owner in raw_owner_keys if owner.strip())
    )
    if not normalized_owners:
        raise ValueError("Topic SSE owner keys must not be empty")

    for owner_key in normalized_owners:
        owner_queue_ids = _topic_owner_queue_ids.get(owner_key, set())
        if len(owner_queue_ids) >= _topic_owner_limit(owner_key):
            if owner_key.startswith(("hop:", "proxy:")):
                detail = "Too many concurrent topic connections through this proxy path"
            else:
                detail = "Too many concurrent topic live-update connections for this client"
            raise SSECapacityError(detail)

    if len(_active_topic_queue_ids) >= _MAX_LOCAL_TOPIC_CONNECTIONS:
        raise SSECapacityError("Topic live updates are temporarily at capacity")

    queue = _new_queue()
    queue_id = id(queue)
    _topic_queues.setdefault(topic, []).append(queue)
    _topic_queue_owners[queue_id] = normalized_owners
    for owner_key in normalized_owners:
        _topic_owner_queue_ids.setdefault(owner_key, set()).add(queue_id)
    _active_topic_queue_ids.add(queue_id)
    return queue


def unregister_topic_queue(topic: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
    queues = _topic_queues.get(topic, [])
    with contextlib.suppress(ValueError):
        queues.remove(queue)

    queue_id = id(queue)
    owner_keys = _topic_queue_owners.pop(queue_id, ())
    for owner_key in owner_keys:
        owner_queue_ids = _topic_owner_queue_ids.get(owner_key)
        if owner_queue_ids is not None:
            owner_queue_ids.discard(queue_id)
            if not owner_queue_ids:
                _topic_owner_queue_ids.pop(owner_key, None)
    _active_topic_queue_ids.discard(queue_id)
    _release_queue(queue)
    if not queues:
        _topic_queues.pop(topic, None)


def _deliver_to_topic(topic: str, event: dict[str, Any]) -> None:
    for queue in list(_topic_queues.get(topic, [])):
        outgoing = event
        master_topics = _master_topic_channels.get(id(queue))
        if master_topics is not None:
            channels = master_topics.get(topic)
            if channels is None:
                continue
            for channel in channels:
                outgoing = {
                    "type": str(event.get("type") or "message"),
                    "channel": channel,
                    "data": event,
                }
                if _enqueue_or_request_resync(queue, outgoing):
                    logger.warning(
                        "SSE topic queue overflow for topic %s; requesting resync", topic
                    )
            continue
        if _enqueue_or_request_resync(queue, event):
            logger.warning("SSE topic queue overflow for topic %s; requesting resync", topic)


def broadcast_to_topic(topic: str, event: dict[str, Any]) -> None:
    """Broadcast an event to all watchers of a topic, across processes."""
    _deliver_to_topic(topic, event)
    _publish("topic", topic, event)


# --- Redis pub/sub bridge ----------------------------------------------------


def _publish(scope: str, key: str, event: dict[str, Any]) -> None:
    """Queue a broadcast for fan-out to other processes (best-effort)."""
    if _publish_queue is None:
        return

    try:
        payload = json.dumps(
            {"origin": _INSTANCE_ID, "scope": scope, "key": key, "event": event}, default=str
        )
        _publish_queue.put_nowait(payload)
    except TypeError:
        logger.error("Failed to serialize SSE event payload for %s", scope)
    except asyncio.QueueFull:
        logger.warning("SSE fan-out queue full; dropping %s broadcast", scope)


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


# --- Reusable SSE event generator ---


async def sse_event_stream(
    queue: asyncio.Queue[dict[str, Any]],
    cleanup: Callable[[], None],
    event_name: str | None = None,
    keepalive_seconds: float = 30.0,
) -> AsyncGenerator[dict[str, Any], None]:
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                if event.get("type") == "close":
                    break

                event_type = str(event.get("type") or "message")
                outgoing_event = (
                    event_type if event_type == "resync_required" else event_name or event_type
                )
                yield {
                    "event": outgoing_event,
                    "data": json.dumps(event, default=str),
                }

                if event_type == "resync_required":
                    break
            except TimeoutError:
                yield {"event": "ping", "data": ""}
    finally:
        cleanup()
