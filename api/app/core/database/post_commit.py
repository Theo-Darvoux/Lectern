import json
import logging
import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.redis as redis_db
from app.core.events.coalesce import coalesce_index_jobs
from app.core.events.sse import broadcast_to_topic
from app.models.outbox import OutboxJob

logger = logging.getLogger(__name__)

_OUTBOX_MAX_ATTEMPTS = 10
_OUTBOX_MAX_BACKOFF_SECONDS = 3600
_OUTBOX_KWARGS_KEY = "__outbox_kwargs__"
_ALLOWED_JOB_NAMES = frozenset(
    {
        "add_cas_references",
        "delete_indexed_item",
        "delete_storage_objects",
        "index_directories_batch",
        "index_directory",
        "index_material",
        "index_materials_batch",
        "process_upload",
        "release_cas_references",
        "release_upload_quota",
    }
)


class PostCommitKey(StrEnum):
    JOBS = "post_commit_jobs"
    SSE = "post_commit_sse_broadcasts"
    JOB_KEYS = "post_commit_job_keys"
    DEINDEX_KEYS = "post_commit_deindex_keys"


def add_post_commit_job(session: AsyncSession, job: tuple[Any, ...]) -> None:
    """Safely append a background job to be executed post-commit."""
    _validate_job(job)
    session.info.setdefault(PostCommitKey.JOBS, []).append(job)


def add_post_commit_sse(session: AsyncSession, topic: str, event: dict[str, Any]) -> None:
    """Safely append an SSE broadcast event to be dispatched post-commit."""
    session.info.setdefault(PostCommitKey.SSE, []).append((topic, event))


def outbox_kwargs(**kwargs: object) -> dict[str, dict[str, object]]:
    """Encode keyword arguments in the JSON-only OutboxJob args column."""
    return {_OUTBOX_KWARGS_KEY: kwargs}


def _normalize_json(value: object) -> object:
    """Return a strict JSON value, explicitly converting supported domain scalars."""
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("outbox arguments cannot contain non-finite numbers")
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("outbox argument mappings must have string keys")
        return {key: _normalize_json(item) for key, item in value.items()}
    raise TypeError(f"unsupported outbox argument type: {type(value).__name__}")


def _validate_job(job: object) -> tuple[str, list[object]]:
    """Validate a job name and normalize its arguments for durable storage."""
    if not isinstance(job, tuple) or not job:
        raise ValueError("outbox jobs must be non-empty tuples")
    name = str(job[0]) if isinstance(job[0], StrEnum) else job[0]
    if not isinstance(name, str) or name not in _ALLOWED_JOB_NAMES:
        raise ValueError(f"unknown outbox job: {name!r}")
    normalized = _normalize_json(job[1:])
    assert isinstance(normalized, list)
    # Verify strict encoder compatibility now rather than at transaction commit.
    json.dumps(normalized, allow_nan=False)
    return name, normalized


async def persist_post_commit_jobs(session: AsyncSession) -> int:
    """Write queued jobs to the DB outbox in the current transaction."""
    jobs: list[tuple[Any, ...]] = session.info.pop(PostCommitKey.JOBS, [])
    session.info.pop(PostCommitKey.JOB_KEYS, None)
    session.info.pop(PostCommitKey.DEINDEX_KEYS, None)
    for job in jobs:
        _validate_job(job)
    coalesced = coalesce_index_jobs(jobs)
    for job in coalesced:
        name, args = _validate_job(job)
        session.add(OutboxJob(job_name=name, args=args))
    return len(coalesced)


async def dispatch_pending_outbox(session: AsyncSession, limit: int = 100) -> int:
    """Attempt delivery of committed outbox rows, leaving failures retryable."""
    if redis_db.arq_pool is None:
        logger.error("ARQ pool unavailable; durable outbox jobs remain pending")
        return 0

    now = datetime.now(UTC)
    rows = list(
        (
            await session.scalars(
                select(OutboxJob)
                .where(
                    OutboxJob.delivered_at.is_(None),
                    OutboxJob.abandoned_at.is_(None),
                    OutboxJob.next_attempt_at <= now,
                )
                .order_by(OutboxJob.next_attempt_at, OutboxJob.created_at, OutboxJob.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    delivered = 0
    for row in rows:
        try:
            args = list(row.args)
            kwargs: dict[str, object] = {}
            if args and isinstance(args[-1], dict):
                encoded_kwargs = args[-1].get(_OUTBOX_KWARGS_KEY)
                if isinstance(encoded_kwargs, dict):
                    kwargs = dict(encoded_kwargs)
                    args.pop()
            enqueue_job = cast(Any, redis_db.arq_pool.enqueue_job)
            await enqueue_job(
                row.job_name, *args, **kwargs, _job_id=f"outbox:{row.id}"
            )
        except Exception as exc:
            row.attempts = (row.attempts or 0) + 1
            row.last_error = str(exc)[:2000]
            if row.attempts >= _OUTBOX_MAX_ATTEMPTS:
                row.abandoned_at = now
                logger.error(
                    "Abandoning durable outbox job %s after %d attempts: %s",
                    row.id,
                    row.attempts,
                    exc,
                )
            else:
                delay = min(
                    _OUTBOX_MAX_BACKOFF_SECONDS,
                    30 * (2 ** max(0, row.attempts - 1)),
                )
                row.next_attempt_at = now + timedelta(seconds=delay)
                logger.error("Failed to enqueue durable outbox job %s: %s", row.id, exc)
        else:
            row.delivered_at = now
            row.attempts = (row.attempts or 0) + 1
            row.last_error = None
            delivered += 1
    await session.commit()
    return delivered


async def dispatch_post_commit_actions(session: AsyncSession) -> None:
    """Dispatch ephemeral SSE events and retry committed durable jobs."""
    try:
        sse_broadcasts = session.info.pop(PostCommitKey.SSE, [])
        if sse_broadcasts:
            for topic, event in sse_broadcasts:
                try:
                    broadcast_to_topic(topic, event)
                except Exception as e:
                    logger.error(
                        "Failed to broadcast SSE post-commit event for topic '%s': %s", topic, e
                    )
        await dispatch_pending_outbox(session)
    except Exception as e:
        logger.error("Unhandled error during post-commit actions dispatching: %s", e, exc_info=True)
