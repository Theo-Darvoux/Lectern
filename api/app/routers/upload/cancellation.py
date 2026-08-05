"""Authoritative, CAS-aware upload cancellation shared by every upload protocol."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import ConflictError, ServiceUnavailableError
from app.core.security.cas import CasReferenceMissingError, decrement_cas_ref
from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
from app.models.upload import Upload
from app.routers.upload.helpers import _QUOTA_KEY_PREFIX, _STATUS_CACHE_PREFIX

logger = logging.getLogger(__name__)

_UPLOAD_CANCEL_TTL = 24 * 3600

type DeleteObject = Callable[[str], Awaitable[None]]
type ReleaseReservation = Callable[[str, Any], Awaitable[None]]
type CleanupOperation = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class UploadCancellationResult:
    """Stable fields captured while the authoritative upload row was locked."""

    found: bool
    upload_id: str
    user_id: str
    quarantine_key: str | None = None
    final_key: str | None = None
    cas_released: bool = False


def upload_cancel_key(upload_id: str) -> str:
    return f"upload:cancel:{upload_id}"


def upload_lifecycle_lock_name(upload_id: str) -> str:
    return f"upload-lifecycle:{upload_id}"


def _thumbnail_owned_by_upload(thumbnail_key: str | None, upload_id: str) -> bool:
    return bool(thumbnail_key and thumbnail_key.endswith(f"/{upload_id}.webp"))


def _owned_non_shared_keys(
    *,
    user_id: str,
    row_quarantine_key: str | None,
    row_final_key: str | None,
    extra_object_keys: Iterable[str],
) -> set[str]:
    quarantine_prefix = f"quarantine/{user_id}/"
    uploads_prefix = f"uploads/{user_id}/"
    row_keys = {
        key
        for key in (row_quarantine_key, row_final_key)
        if key and (key.startswith(quarantine_prefix) or key.startswith(uploads_prefix))
    }
    # Protocol state was already ownership-checked by the caller. Accept its
    # explicit non-CAS keys even when a test/backend uses a nonstandard prefix.
    protocol_keys = {key for key in extra_object_keys if key and not key.startswith("cas/")}
    return row_keys | protocol_keys


async def _clear_cached_upload_state(
    redis: Redis[Any],
    *,
    quarantine_key: str | None,
    user_id: str,
    original_sha256: str | None,
) -> None:
    keys: list[str] = []
    if quarantine_key:
        keys.extend(
            (
                f"{_STATUS_CACHE_PREFIX}{quarantine_key}",
                f"upload:eventlog:{quarantine_key}",
            )
        )
    if original_sha256:
        keys.append(f"upload:sha256:{user_id}:{original_sha256}")
    if keys:
        await redis.delete(*keys)


async def _release_quota_members(
    redis: Redis[Any],
    *,
    upload_id: str,
    user_id: str,
    object_keys: set[str],
) -> None:
    quota_key = f"{_QUOTA_KEY_PREFIX}{user_id}"
    staging_key = f"staging:{user_id}:{upload_id}"
    raw_members = await redis.zrange(quota_key, 0, -1)
    members = {value.decode() if isinstance(value, bytes) else str(value) for value in raw_members}
    cleanup_members = {staging_key, *object_keys}
    cleanup_members.update(
        value
        for value in members
        if value == staging_key
        or value.startswith(f"quarantine/{user_id}/{upload_id}/")
        or value.startswith(f"uploads/{user_id}/{upload_id}/")
    )
    if cleanup_members:
        await redis.zrem(quota_key, *cleanup_members)


async def cancel_upload_lifecycle(
    *,
    upload_id: str,
    user_id: str,
    redis: Redis[Any],
    db: AsyncSession,
    reason: str,
    delete_object_fn: DeleteObject,
    release_reservation_fn: ReleaseReservation,
    cleanup_operations: Iterable[CleanupOperation] = (),
    extra_object_keys: Iterable[str] = (),
    known_owned: bool = False,
) -> UploadCancellationResult:
    """Cancel one upload and release all lifecycle ownership exactly once.

    Callers must hold :func:`upload_lifecycle_lock_name` for ``upload_id``.
    Protocol callers may additionally hold their protocol-state lock and only
    delete that retry state after this function succeeds.
    """

    owner_id = uuid.UUID(user_id)
    row = await db.scalar(select(Upload).where(Upload.upload_id == upload_id).with_for_update())
    if row is not None and row.user_id != owner_id:
        if known_owned:
            raise ServiceUnavailableError(
                "Upload cancellation ownership is inconsistent. Retry after reconciliation."
            )
        return UploadCancellationResult(found=False, upload_id=upload_id, user_id=user_id)
    if row is None and not known_owned:
        return UploadCancellationResult(found=False, upload_id=upload_id, user_id=user_id)

    if row is not None and row.status == "applied":
        raise ConflictError("This upload has already been applied and can no longer be cancelled")

    if row is not None:
        claimed_keys = [key for key in (row.quarantine_key, row.final_key) if key]
        if claimed_keys:
            active_claim = await db.scalar(
                select(PRFileClaim.file_key)
                .join(PullRequest, PullRequest.id == PRFileClaim.pr_id)
                .where(
                    PRFileClaim.file_key.in_(claimed_keys),
                    PullRequest.status == PRStatus.OPEN,
                    PullRequest.author_id == owner_id,
                )
                .limit(1)
            )
            if active_claim is not None:
                raise ConflictError(
                    "This upload is attached to an open contribution. "
                    "Cancel or reject the contribution before deleting the upload."
                )

    row_quarantine_key = row.quarantine_key if row is not None else None
    row_final_key = row.final_key if row is not None else None
    raw_thumbnail_key = getattr(row, "thumbnail_key", None) if row is not None else None
    row_thumbnail_key = (
        raw_thumbnail_key if isinstance(raw_thumbnail_key, str) and raw_thumbnail_key else None
    )
    original_sha256 = row.sha256 if row is not None else None
    content_sha256 = None
    cas_ref_count = 0
    if row is not None:
        content_sha256 = row.content_sha256 or row.sha256
        cas_ref_count = int(row.cas_ref_count or 0)
        row.status = "cancelled"
        row.error_detail = reason
        await db.commit()

    # The DB row is authoritative. The Redis marker gives active workers a fast
    # cancellation check and is intentionally written before any fallible cleanup.
    try:
        await redis.set(upload_cancel_key(upload_id), "1", ex=_UPLOAD_CANCEL_TTL)
        await _clear_cached_upload_state(
            redis,
            quarantine_key=row_quarantine_key,
            user_id=user_id,
            original_sha256=original_sha256,
        )
    except Exception as exc:
        raise ServiceUnavailableError(
            "Upload was cancelled, but cancellation coordination is incomplete. Retry cancellation."
        ) from exc

    cas_released = False
    if cas_ref_count > 0:
        if not content_sha256:
            raise ServiceUnavailableError(
                "Upload was cancelled, but its CAS ownership record is incomplete. Retry cancellation."
            )
        try:
            await decrement_cas_ref(
                redis,
                content_sha256,
                operation_id=f"cancel-upload:{upload_id}:release",
            )
        except CasReferenceMissingError:
            logger.warning("CAS cache entry already absent while cancelling %s", upload_id)
        except Exception as exc:
            raise ServiceUnavailableError(
                "Upload was cancelled, but CAS ownership could not be released. Retry cancellation."
            ) from exc

        # The operation ID makes the Redis decrement idempotent. Persisting zero
        # afterwards makes a retry safe even when the first DB commit was lost.
        locked_row = await db.scalar(
            select(Upload)
            .where(Upload.upload_id == upload_id, Upload.user_id == owner_id)
            .with_for_update()
        )
        if locked_row is not None and locked_row.cas_ref_count > 0:
            locked_row.cas_ref_count = 0
            await db.commit()
        cas_released = True

    object_keys = _owned_non_shared_keys(
        user_id=user_id,
        row_quarantine_key=row_quarantine_key,
        row_final_key=row_final_key,
        extra_object_keys=extra_object_keys,
    )
    if row_thumbnail_key and _thumbnail_owned_by_upload(row_thumbnail_key, upload_id):
        object_keys.add(row_thumbnail_key)

    cleanup_errors: list[BaseException] = []
    for operation in cleanup_operations:
        try:
            await operation()
        except Exception as exc:
            cleanup_errors.append(exc)
    for object_key in object_keys:
        try:
            await delete_object_fn(object_key)
        except Exception as exc:
            cleanup_errors.append(exc)

    if cleanup_errors:
        logger.warning(
            "Cancellation for upload %s retained retry state after cleanup failure: %s",
            upload_id,
            cleanup_errors[0],
        )
        raise ServiceUnavailableError(
            "Upload was cancelled, but storage cleanup is incomplete. Retry cancellation."
        ) from cleanup_errors[0]

    if row is not None and row_thumbnail_key:
        thumbnail_row = await db.scalar(
            select(Upload)
            .where(Upload.upload_id == upload_id, Upload.user_id == owner_id)
            .with_for_update()
        )
        if thumbnail_row is not None:
            thumbnail_row.thumbnail_key = None
            thumbnail_row.thumbnail_status = "failed"
            await db.commit()

    try:
        await _release_quota_members(
            redis,
            upload_id=upload_id,
            user_id=user_id,
            object_keys=object_keys,
        )
        await release_reservation_fn(upload_id, redis)
    except Exception as exc:
        raise ServiceUnavailableError(
            "Upload was cancelled, but quota cleanup is incomplete. Retry cancellation."
        ) from exc

    return UploadCancellationResult(
        found=row is not None or known_owned,
        upload_id=upload_id,
        user_id=user_id,
        quarantine_key=row_quarantine_key,
        final_key=row_final_key,
        cas_released=cas_released,
    )
