import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.database.database import get_db
from app.core.events.limiter import limiter
from app.core.events.sse import (
    SSEStream,
    notifications_topic,
    register_stream,
    stream_events,
    unregister_stream,
    uploads_topic,
)
from app.dependencies.auth import SSEUser
from app.dependencies.rate_limit import request_session_id
from app.models.directory import Directory
from app.models.material import Material

router = APIRouter(prefix="/api/events", tags=["events"])

_MAX_ENTITY_TOPICS = 20


def parse_topics(user_id: uuid.UUID, topics: list[str]) -> dict[str, str]:
    """Translate public channel names into internal topic keys.

    Unknown or malformed entity topics are dropped: one stale interest must not
    cost a browser its notifications.
    """
    parsed = {
        "notifications": notifications_topic(user_id),
        "pull_requests": f"pr_updates:{user_id}",
        "uploads": uploads_topic(user_id),
    }
    entity_count = 0
    for raw_topic in dict.fromkeys(topics):
        if entity_count >= _MAX_ENTITY_TOPICS:
            break
        kind, _, raw_id = raw_topic.partition(":")
        if kind == "directory" and raw_id == "root":
            parsed["directory:root"] = "root"
            entity_count += 1
        elif kind in {"directory", "material"}:
            with suppress(ValueError):
                entity_id = str(uuid.UUID(raw_id))
                parsed[f"{kind}:{entity_id}"] = entity_id
                entity_count += 1
    return parsed


async def _existing_ids(db: AsyncSession, model: Any, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    result = await db.execute(select(model.id).where(model.id.in_(ids), model.deleted_at.is_(None)))
    return set(result.scalars())


async def _drop_deleted_entities(
    db: AsyncSession,
    topic_channels: dict[str, str],
) -> dict[str, str]:
    """Keep only live entities: a deleted branch may linger in a client's tree."""

    def ids_of(prefix: str) -> set[uuid.UUID]:
        return {
            uuid.UUID(topic)
            for channel, topic in topic_channels.items()
            if channel.startswith(prefix) and channel != "directory:root"
        }

    live_ids = await _existing_ids(db, Material, ids_of("material:"))
    live_ids |= await _existing_ids(db, Directory, ids_of("directory:"))
    return {
        channel: topic
        for channel, topic in topic_channels.items()
        if not channel.startswith(("material:", "directory:"))
        or channel == "directory:root"
        or uuid.UUID(topic) in live_ids
    }


@dataclass
class _StreamSlot:
    stream: SSEStream | None = None


async def _stream_slot() -> AsyncGenerator[_StreamSlot, None]:
    """Release the stream when the response ends, even if its body never started.

    The stream is opened in the route body so a rate-limited request cannot
    replace the browser's working stream.
    """
    slot = _StreamSlot()
    try:
        yield slot
    finally:
        if slot.stream is not None:
            unregister_stream(slot.stream)


def _stream_rate_limit_key(request: Request) -> str:
    # Keyed per session: a campus NAT shares one address across many students.
    return request_session_id(request) or get_remote_address(request)


@router.get("/sse")
@limiter.limit("60/minute", key_func=_stream_rate_limit_key)
async def master_event_stream(
    request: Request,
    user: SSEUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    slot: Annotated[_StreamSlot, Depends(_stream_slot)],
    client: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
    topic: Annotated[list[str] | None, Query()] = None,
) -> EventSourceResponse:
    """One authenticated stream per browser, multiplexing every live-update channel."""
    topic_channels = await _drop_deleted_entities(db, parse_topics(user.id, topic or []))
    # get_db only tears down after the stream ends; release the pooled connection
    # now instead of pinning it idle-in-transaction for the stream's lifetime.
    await db.commit()

    slot.stream = register_stream(user.id, client, topic_channels)
    return EventSourceResponse(
        stream_events(slot.stream),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
