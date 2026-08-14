import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.common.exceptions import NotFoundError
from app.core.database.database import get_db
from app.core.database.redis import RedisLockTimeoutError, get_redis, redis_lock
from app.core.events.limiter import limiter
from app.core.events.sse import (
    SSECapacityError,
    register_master_queue,
    sse_event_stream,
    unregister_master_queue,
)
from app.dependencies.auth import SSEUser
from app.models.directory import Directory
from app.models.material import Material

router = APIRouter(prefix="/api/events", tags=["events"])

_MAX_MASTER_TOPICS = 20


def parse_master_topics(user_id: uuid.UUID, topics: list[str]) -> dict[str, str]:
    """Translate public channel names into the existing internal topic keys."""
    if len(topics) > _MAX_MASTER_TOPICS:
        raise NotFoundError("Live-update topic not found")

    parsed = {"pull_requests": f"pr_updates:{user_id}"}
    for raw_topic in dict.fromkeys(topics):
        try:
            kind, raw_id = raw_topic.split(":", 1)
            if kind == "directory" and raw_id == "root":
                parsed["directory:root"] = "root"
                continue
            if kind not in {"directory", "material"}:
                raise ValueError
            entity_id = str(uuid.UUID(raw_id))
        except (ValueError, AttributeError):
            raise NotFoundError("Live-update topic not found")
        parsed[f"{kind}:{entity_id}"] = entity_id
    return parsed


async def _validate_entity_topics(
    db: AsyncSession,
    topic_channels: dict[str, str],
) -> None:
    material_ids = {
        uuid.UUID(topic.removeprefix("material:"))
        for topic in topic_channels
        if topic.startswith("material:")
    }
    directory_ids = {
        uuid.UUID(topic.removeprefix("directory:"))
        for topic in topic_channels
        if topic.startswith("directory:") and topic != "directory:root"
    }

    if material_ids:
        result = await db.execute(
            select(Material.id).where(
                Material.id.in_(material_ids),
                Material.deleted_at.is_(None),
            )
        )
        if set(result.scalars()) != material_ids:
            raise NotFoundError("Live-update topic not found")

    if directory_ids:
        result = await db.execute(
            select(Directory.id).where(
                Directory.id.in_(directory_ids),
                Directory.deleted_at.is_(None),
            )
        )
        if set(result.scalars()) != directory_ids:
            raise NotFoundError("Live-update topic not found")


async def _hold_master_lease(
    user: SSEUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> AsyncGenerator[None, None]:
    """Reject duplicate streams before responding and hold the lease until disconnect."""
    try:
        async with redis_lock(
            redis,
            f"master_sse:{user.id}",
            timeout=0.1,
            retry_interval=0.05,
            expire=30,
        ):
            yield
    except RedisLockTimeoutError as exc:
        raise SSECapacityError(
            "A master live-update connection is already active for this user"
        ) from exc

@router.get("/sse")
@limiter.limit("60/minute")
async def master_event_stream(
    request: Request,
    user: SSEUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _lease: Annotated[None, Depends(_hold_master_lease)],
    topic: Annotated[list[str] | None, Query()] = None,
) -> EventSourceResponse:
    """One authenticated stream multiplexing every live-update channel."""
    topic_channels = parse_master_topics(user.id, topic or [])
    await _validate_entity_topics(db, topic_channels)

    queue = register_master_queue(user.id, topic_channels)
    return EventSourceResponse(
        sse_event_stream(
            queue,
            cleanup=lambda: unregister_master_queue(user.id, queue),
        ),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
