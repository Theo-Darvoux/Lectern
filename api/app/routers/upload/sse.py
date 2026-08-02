"""SSE stream and status polling for upload processing progress."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.common.exceptions import ForbiddenError, RateLimitError
from app.core.database.database import get_db
from app.core.database.redis import get_redis
from app.dependencies.auth import CurrentUser
from app.models.upload import Upload
from app.routers.upload.helpers import _STATUS_CACHE_PREFIX
from app.schemas.material import UploadStatus, UploadStatusOut

logger = logging.getLogger(__name__)

router = APIRouter()

_SSE_TIMEOUT = 600.0  # 10 min -- maximum SSE stream duration
_SSE_KEEPALIVE = 15.0  # seconds between keepalive pings (issue 4.10)
_SSE_MAX_PER_USER = 10  # max concurrent SSE streams per user (issue 1.14)
_SSE_COUNTER_PREFIX = "upload:sse:active:"
_SSE_COUNTER_TTL = 700  # slightly longer than _SSE_TIMEOUT as a safety net
_SSE_HANDOFF_QUEUE_SIZE = 256


@asynccontextmanager
async def sse_concurrency_guard(redis: Redis, user_id: str):  # type: ignore[no-untyped-def,type-arg]
    """Async context manager to track and limit concurrent SSE streams per user."""
    sse_counter_key = f"{_SSE_COUNTER_PREFIX}{user_id}"
    _sse_count = await redis.incr(sse_counter_key)
    if _sse_count == 1:
        await redis.expire(sse_counter_key, _SSE_COUNTER_TTL)
    if _sse_count > _SSE_MAX_PER_USER:
        await redis.decr(sse_counter_key)
        raise RateLimitError(f"Too many concurrent SSE streams (max {_SSE_MAX_PER_USER} per user)")
    try:
        yield
    finally:
        with suppress(Exception):
            await redis.decr(sse_counter_key)


async def _load_event_log(
    redis: Redis,  # type: ignore[type-arg]
    event_log_key: str,
    *,
    start: int = 0,
) -> list[str]:
    """Load the bounded upload log from a best-effort SSE replay offset."""
    raw_entries = await redis.lrange(event_log_key, max(0, start), -1)
    return [raw.decode() if isinstance(raw, bytes) else str(raw) for raw in raw_entries]


def _enqueue_pubsub_payload(
    queue: asyncio.Queue[str | None],
    payload: str,
) -> bool:
    """Offer a pub/sub event without allowing a slow client to grow memory."""
    try:
        queue.put_nowait(payload)
        return True
    except asyncio.QueueFull:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait(None)
        return False


def _upload_sse_event(
    payload: str,
    *,
    event_id: int | str | None = None,
) -> dict[str, str]:
    """Format an upload event; only durable replay entries receive an SSE cursor."""
    event = {"event": "upload", "data": payload}
    if event_id is not None:
        event["id"] = str(event_id)
    return event


async def _check_file_ownership(file_key: str, user_id: str, db: AsyncSession) -> None:
    """Raise ForbiddenError if the file_key doesn't belong to the user."""
    # V1 keys: quarantine/{user_id}/... or uploads/{user_id}/...
    if file_key.startswith(f"quarantine/{user_id}/") or file_key.startswith(f"uploads/{user_id}/"):
        return

    # V2 keys: cas/{hmac} (ownership verified via Upload table)
    if file_key.startswith("cas/"):
        # Check if this user has any upload record pointing to this CAS key.
        # Ensure user_id is a UUID object for SQLAlchemy type processing.
        uid = uuid.UUID(str(user_id))
        exists = await db.scalar(
            select(Upload.id).where(Upload.final_key == file_key, Upload.user_id == uid).limit(1)
        )
        if exists:
            return

    raise ForbiddenError("File does not belong to you")


@router.get("/status/{file_key:path}", response_model=UploadStatusOut)
async def upload_status(
    file_key: str,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadStatusOut:
    """Non-SSE status poll for upload processing.

    Returns the cached status written by the background worker.
    Returns PENDING if no status has been written yet.
    """
    await _check_file_ownership(file_key, str(user.id), db)

    cached = await redis.get(f"{_STATUS_CACHE_PREFIX}{file_key}")

    # ── Database Fallback (Issue 6) ──
    if not cached:
        # Try to find via file_key (final_key) or upload_id extracted from path
        row = await db.scalar(
            select(Upload).where(Upload.final_key == file_key, Upload.user_id == user.id)
        )

        if not row and "/" in file_key:
            parts = file_key.split("/")
            if len(parts) >= 3:
                upload_id = parts[2]
                row = await db.scalar(
                    select(Upload).where(Upload.upload_id == upload_id, Upload.user_id == user.id)
                )

        if row and row.status in ("clean", "failed", "malicious"):
            res_data = {
                "upload_id": row.upload_id,
                "file_key": file_key,
                "status": row.status,
                "detail": row.error_detail or ("Success" if row.status == "clean" else "Failed"),
                "result": {
                    "file_key": row.final_key or file_key,
                    "size": row.size_bytes,
                    "original_size": row.size_bytes,
                    "mime_type": row.mime_type,
                    "file_name": row.filename,
                }
                if row.status == "clean"
                else None,
            }
            cached = json.dumps(res_data)

    if not cached:
        return UploadStatusOut(file_key=file_key, status=UploadStatus.PENDING)

    try:
        return UploadStatusOut(**json.loads(cached))
    except Exception:
        return UploadStatusOut(file_key=file_key, status=UploadStatus.PENDING)


@router.get("/events/{file_key:path}")
async def upload_events(
    file_key: str,
    request: Request,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EventSourceResponse:
    """SSE stream for upload processing status.

    Auth via Authorization: Bearer header (fetch-based SSE, not native EventSource).
    Reconnect-safe: replays the bounded durable log at least once on reconnect.

    Events:
      - type=upload, data=UploadStatusOut JSON  (status updates from worker)
      - type=ping,   data=""                    (keepalive)
    """
    user_id = str(user.id)
    await _check_file_ownership(file_key, user_id, db)

    # Pre-compute values needed by the generator before it starts
    cached_status: str | None = await redis.get(f"{_STATUS_CACHE_PREFIX}{file_key}")

    # ── Database Fallback (Issue 6) ──
    if not cached_status:
        # Try to find via file_key (final_key) or upload_id extracted from path
        row = await db.scalar(
            select(Upload).where(Upload.final_key == file_key, Upload.user_id == user.id)
        )

        if not row and "/" in file_key:
            parts = file_key.split("/")
            if len(parts) >= 3:
                upload_id = parts[2]
                row = await db.scalar(
                    select(Upload).where(Upload.upload_id == upload_id, Upload.user_id == user.id)
                )

        if row and row.status in ("clean", "failed", "malicious"):
            res_data = {
                "upload_id": row.upload_id,
                "file_key": file_key,
                "status": row.status,
                "detail": row.error_detail or ("Success" if row.status == "clean" else "Failed"),
                "result": {
                    "file_key": row.final_key or file_key,
                    "size": row.size_bytes,
                    "original_size": row.size_bytes,
                    "mime_type": row.mime_type,
                    "file_name": row.filename,
                }
                if row.status == "clean"
                else None,
            }
            cached_status = json.dumps(res_data)

    # Short-circuit if terminal status is cached.
    # Replay the full event log first so the client sees all intermediate stage
    # messages (scanning, compressing, etc.) even when processing already finished.
    if cached_status:
        try:
            data = json.loads(cached_status)
            if data.get("status") in ("clean", "malicious", "failed"):
                event_log_key = f"upload:eventlog:{file_key}"
                log_entries = await _load_event_log(redis, event_log_key)
                events: list[dict[str, str]] = []
                for i, entry in enumerate(log_entries):
                    events.append(_upload_sse_event(entry, event_id=i + 1))
                # Ensure the terminal event is present (it should be the last log entry,
                # but append cached_status as a safety net if the log is empty).
                if not events:
                    events.append(_upload_sse_event(cached_status))
                return EventSourceResponse(
                    AsyncIteratorAdapter(events),  # type: ignore[no-untyped-call]
                    headers={"X-Accel-Buffering": "no"},
                )
        except (json.JSONDecodeError, KeyError):
            pass

    try:
        last_event_id = max(0, int(request.headers.get("Last-Event-ID", "0")))
    except (ValueError, TypeError):
        last_event_id = 0

    # Eagerly check the concurrency limit for fast 429 rejection.
    # The actual counter lifecycle (incr/decr) is managed inside the generator
    # so the decrement happens when the stream ends, not when the endpoint returns.
    sse_counter_key = f"{_SSE_COUNTER_PREFIX}{user_id}"
    _pre_count = await redis.incr(sse_counter_key)
    if _pre_count == 1:
        await redis.expire(sse_counter_key, _SSE_COUNTER_TTL)
    if _pre_count > _SSE_MAX_PER_USER:
        await redis.decr(sse_counter_key)
        raise RateLimitError(f"Too many concurrent SSE streams (max {_SSE_MAX_PER_USER} per user)")

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        # Counter was already incremented eagerly; decrement when the stream ends.
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe(f"upload:events:{file_key}")

            queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_SSE_HANDOFF_QUEUE_SIZE)

            async def _pubsub_reader() -> None:
                try:
                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        raw_payload = message["data"]
                        payload = (
                            raw_payload.decode()
                            if isinstance(raw_payload, bytes)
                            else str(raw_payload)
                        )
                        if not _enqueue_pubsub_payload(queue, payload):
                            logger.warning(
                                "Upload SSE handoff queue overflow for %s; closing for replay",
                                file_key,
                            )
                            return
                        try:
                            if json.loads(payload).get("status") in (
                                "clean",
                                "malicious",
                                "failed",
                            ):
                                await queue.put(None)
                                return
                        except (json.JSONDecodeError, KeyError):
                            pass
                except Exception as exc:
                    logger.warning("Pub/Sub reader error for %s: %s", file_key, exc)
                    await queue.put(None)

            reader_task = asyncio.create_task(_pubsub_reader())

            # Preserve the existing Last-Event-ID contract as a best-effort
            # offset into the bounded Redis list. The list may be trimmed, so this
            # is not an absolute sequence, but it remains useful for ordinary
            # reconnects. Do not skip pub/sub messages based on the current log
            # length: those messages can be genuinely new events.
            event_log_key = f"upload:eventlog:{file_key}"
            replayed = await _load_event_log(
                redis,
                event_log_key,
                start=last_event_id,
            )

            yielded_count = last_event_id

            for i, payload_str in enumerate(replayed):
                yielded_count = last_event_id + i + 1
                yield _upload_sse_event(payload_str, event_id=yielded_count)
                try:
                    if json.loads(payload_str).get("status") in (
                        "clean",
                        "malicious",
                        "failed",
                    ):
                        reader_task.cancel()
                        return
                except (json.JSONDecodeError, KeyError):
                    pass

            # Stream from Pub/Sub queue
            try:
                deadline = asyncio.get_running_loop().time() + _SSE_TIMEOUT
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break

                    try:
                        payload = await asyncio.wait_for(
                            queue.get(),
                            timeout=min(_SSE_KEEPALIVE, remaining),
                        )
                    except TimeoutError:
                        yield {"event": "ping", "data": ""}
                        continue

                    if payload is None:
                        break

                    # Pub/sub can duplicate an event already observed during the
                    # subscribe-before-replay window. Never advance Last-Event-ID
                    # for live messages: reconnects replay from the last durable
                    # Redis-list cursor, yielding harmless duplicates instead of gaps.
                    yield _upload_sse_event(payload)

                    try:
                        if json.loads(payload).get("status") in (
                            "clean",
                            "malicious",
                            "failed",
                        ):
                            break
                    except (json.JSONDecodeError, KeyError):
                        pass

            finally:
                reader_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reader_task
                try:
                    await pubsub.unsubscribe(f"upload:events:{file_key}")
                    await pubsub.reset()
                except Exception:
                    pass
        finally:
            # Decrement the concurrency counter when the stream ends
            with suppress(Exception):
                await redis.decr(sse_counter_key)

    return EventSourceResponse(
        event_generator(),
        headers={"X-Accel-Buffering": "no"},
    )


class AsyncIteratorAdapter:
    """Adapts a plain list into a proper async iterator."""

    def __init__(self, items: list[dict[str, str]]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> "AsyncIteratorAdapter":
        return self

    async def __anext__(self) -> dict[str, str]:
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration
