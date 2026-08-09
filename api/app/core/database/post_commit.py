import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.redis as redis_db
from app.core.events.coalesce import coalesce_index_jobs
from app.core.events.sse import broadcast_to_topic, broadcast_to_user
from app.core.security.async_utils import settle_awaitable
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
        "dispatch_webhook",
        "index_directories_batch",
        "index_directory",
        "index_material",
        "index_materials_batch",
        "process_upload",
        "process_upload_post_scan",
        "release_cas_references",
        "release_storage_reservations",
        "release_upload_quota",
    }
)


class PostCommitKey(StrEnum):
    JOBS = "post_commit_jobs"
    SSE = "post_commit_sse_broadcasts"
    USER_SSE = "post_commit_user_sse_broadcasts"
    JOB_KEYS = "post_commit_job_keys"
    DEINDEX_KEYS = "post_commit_deindex_keys"
    MANAGED_TRANSACTION = "managed_request_transaction"
    TRANSACTION_COMMIT_CALLBACKS = "transaction_commit_callbacks"
    TRANSACTION_ROLLBACK_CALLBACKS = "transaction_rollback_callbacks"


TransactionCallback = Callable[[], Awaitable[None]]


def register_transaction_callbacks(
    session: AsyncSession,
    *,
    on_rollback: TransactionCallback,
    on_commit: TransactionCallback,
) -> bool:
    """Tie external-resource compensation to the request-owned DB transaction.

    Returns ``False`` for caller-owned sessions so services can preserve their
    existing immediate finalization semantics outside the FastAPI dependency.
    """
    if session.info.get(PostCommitKey.MANAGED_TRANSACTION) is not True:
        return False
    session.info.setdefault(PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS, []).append(on_rollback)
    session.info.setdefault(PostCommitKey.TRANSACTION_COMMIT_CALLBACKS, []).append(on_commit)
    return True


async def rollback_transaction_callbacks(session: AsyncSession) -> None:
    """Compensate external mutations in reverse registration order."""
    callbacks: list[TransactionCallback] = session.info.pop(
        PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS, []
    )
    session.info.pop(PostCommitKey.TRANSACTION_COMMIT_CALLBACKS, None)
    errors: list[BaseException] = []
    caller_cancellation: asyncio.CancelledError | None = None
    for callback in reversed(callbacks):
        _result, error, cancellation = await settle_awaitable(callback())
        caller_cancellation = caller_cancellation or cancellation
        if error is not None:
            errors.append(error)
            logger.error(
                "External transaction compensation failed",
                exc_info=(type(error), error, error.__traceback__),
            )
    if errors:
        raise RuntimeError(
            f"{len(errors)} external transaction compensation callback(s) failed"
        ) from errors[0]
    if caller_cancellation is not None:
        raise caller_cancellation


async def finalize_transaction_callbacks(session: AsyncSession) -> None:
    """Finalize external mutations after a successful database commit."""
    callbacks: list[TransactionCallback] = session.info.pop(
        PostCommitKey.TRANSACTION_COMMIT_CALLBACKS, []
    )
    session.info.pop(PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS, None)
    caller_cancellation: asyncio.CancelledError | None = None
    for callback in callbacks:
        _result, error, cancellation = await settle_awaitable(callback())
        caller_cancellation = caller_cancellation or cancellation
        if error is not None:
            # The database commit is already durable. Finalizers are restricted
            # to cleanup and must never make a successful mutation look failed.
            logger.error(
                "External transaction finalization failed after database commit",
                exc_info=(type(error), error, error.__traceback__),
            )
    if caller_cancellation is not None:
        raise caller_cancellation


def add_post_commit_job(session: AsyncSession, job: tuple[Any, ...]) -> None:
    """Safely append a background job to be executed post-commit."""
    _validate_job(job)
    session.info.setdefault(PostCommitKey.JOBS, []).append(job)


def add_post_commit_sse(session: AsyncSession, topic: str, event: dict[str, Any]) -> None:
    """Safely append an SSE broadcast event to be dispatched post-commit."""
    session.info.setdefault(PostCommitKey.SSE, []).append((topic, event))


def add_post_commit_user_sse(
    session: AsyncSession,
    user_id: UUID,
    event: dict[str, Any],
) -> None:
    """Queue a user notification broadcast only after transaction commit."""
    session.info.setdefault(PostCommitKey.USER_SSE, []).append((user_id, event))


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
            await enqueue_job(row.job_name, *args, **kwargs, _job_id=f"outbox:{row.id}")
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
        user_broadcasts = session.info.pop(PostCommitKey.USER_SSE, [])
        for user_id, event in user_broadcasts:
            try:
                broadcast_to_user(user_id, event)
            except Exception as e:
                logger.error(
                    "Failed to broadcast SSE post-commit event for user '%s': %s", user_id, e
                )
        await dispatch_pending_outbox(session)
    except Exception as e:
        logger.error("Unhandled error during post-commit actions dispatching: %s", e, exc_info=True)
