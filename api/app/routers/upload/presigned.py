"""Presigned upload endpoints: single-part and multipart."""

import contextlib
import json
import logging
import time
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.constants import PRIVILEGED_ROLES
from app.core.common.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    ServiceUnavailableError,
)
from app.core.common.upload_errors import UploadErrorCode
from app.core.database.database import get_db
from app.core.database.redis import get_redis, redis_lock
from app.core.media.mimetypes import MimeRegistry, guess_mime_from_bytes
from app.core.storage.facade import (
    abort_multipart_upload,
    create_multipart_upload,
    delete_object,
    generate_presigned_put,
    generate_presigned_upload_part,
    get_object_info,
)
from app.core.storage.multipart_completion import (
    MultipartCompletionError,
    complete_multipart_verified,
)
from app.dependencies.auth import CurrentUser
from app.dependencies.rate_limit import rate_limit_uploads
from app.models.upload import Upload
from app.routers.upload.helpers import (
    _QUOTA_KEY_PREFIX,
    _UPLOAD_INTENT_PREFIX,
    _UPLOAD_INTENT_TTL,
    _check_pending_cap,
    _create_upload_row,
    _enqueue_processing,
    _release_storage_reservation,
    _reserve_storage_limit,
)
from app.routers.upload.validators import (
    _apply_mime_correction,
    _check_per_type_size,
    _validate_filename,
)
from app.schemas.material import (
    PresignedMultipartCompleteRequest,
    PresignedMultipartInitOut,
    PresignedMultipartPart,
    PresignedUploadOut,
    UploadCompleteRequest,
    UploadInitRequest,
    UploadPendingOut,
    UploadStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── POST /api/upload/init ────────────────────────────────────────────────────


_PRESIGNED_DEPRECATION_HEADERS = {
    "Deprecation": "true",
    "Sunset": "Sat, 01 Jan 2027 00:00:00 GMT",
    "Link": '</api/upload>; rel="successor-version"',
}
_PRESIGNED_MULTIPART_PART_SIZE = 8 * 1024 * 1024
_UPLOAD_CANCEL_TTL = 24 * 3600


def _upload_cancel_key(upload_id: str) -> str:
    return f"upload:cancel:{upload_id}"


async def _presigned_upload_is_cancelled(
    intent: dict[str, Any],
    redis: Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> bool:
    if intent.get("cancelled"):
        return True
    try:
        if await redis.get(_upload_cancel_key(str(intent["upload_id"]))):
            return True
    except Exception as exc:
        raise ServiceUnavailableError(
            "Upload cancellation state is temporarily unavailable. Retry the request."
        ) from exc
    status = await db.scalar(select(Upload.status).where(Upload.upload_id == intent["upload_id"]))
    return status == "cancelled"


async def _ensure_presigned_upload_active(
    intent: dict[str, Any],
    redis: Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> None:
    if await _presigned_upload_is_cancelled(intent, redis, db):
        raise ConflictError("Upload was cancelled")


def _validated_multipart_manifest(
    data: PresignedMultipartCompleteRequest,
    intent: dict[str, Any],
) -> list[dict[str, int | str]]:
    """Validate a completion manifest against the server-created upload intent."""
    try:
        declared_size = int(intent["size"])
        part_size = int(intent.get("part_size", _PRESIGNED_MULTIPART_PART_SIZE))
        expected_parts = int(intent.get("num_parts", (declared_size + part_size - 1) // part_size))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise BadRequestError(
            "Multipart upload intent is invalid. Please restart the upload.",
            code=UploadErrorCode.INTENT_MISMATCH,
        ) from exc

    if declared_size < 1 or part_size < 1 or not 1 <= expected_parts <= 10_000:
        raise BadRequestError(
            "Multipart upload intent is invalid. Please restart the upload.",
            code=UploadErrorCode.INTENT_MISMATCH,
        )

    expected_numbers = set(range(1, expected_parts + 1))
    actual_numbers = [part.PartNumber for part in data.parts]
    if len(actual_numbers) != expected_parts or set(actual_numbers) != expected_numbers:
        raise BadRequestError(
            "Multipart completion must contain each expected part exactly once.",
            code=UploadErrorCode.INTENT_MISMATCH,
        )

    return [
        {"PartNumber": part.PartNumber, "ETag": part.ETag}
        for part in sorted(data.parts, key=lambda part: part.PartNumber)
    ]


async def _discard_multipart_intent(
    intent: dict[str, Any],
    *,
    redis: Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    reason: str,
) -> None:
    """Delete terminal multipart data before releasing its tracking state."""
    with contextlib.suppress(Exception):
        await abort_multipart_upload(intent["quarantine_key"], intent["s3_multipart_id"])
    try:
        await delete_object(intent["quarantine_key"])
    except Exception as exc:
        # Retain intent and reservation so a retry or cleanup worker can finish
        # deletion. Never turn a failed quarantine cleanup into silent success.
        raise ServiceUnavailableError(
            "The invalid multipart object could not be removed. Retry completion or abort."
        ) from exc

    await redis.delete(f"{_UPLOAD_INTENT_PREFIX}{intent['upload_id']}")
    await _release_storage_reservation(intent["upload_id"], redis)
    await redis.zrem(
        f"{_QUOTA_KEY_PREFIX}{intent['user_id']}",
        intent["quarantine_key"],
    )
    await db.execute(
        sql_update(Upload)
        .where(Upload.upload_id == intent["upload_id"], Upload.status != "cancelled")
        .values(status="failed", error_detail=reason)
    )
    await db.commit()


@router.post("/init", response_model=PresignedUploadOut)
async def init_upload(
    data: UploadInitRequest,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_uploads)],
    response: Response,
) -> PresignedUploadOut:
    """Request a presigned PUT URL for direct-to-S3 upload."""
    for k, v in _PRESIGNED_DEPRECATION_HEADERS.items():
        response.headers[k] = v
    user_id = str(user.id)

    safe_name, _ext = _validate_filename(data.filename)
    resolved_mime = MimeRegistry.resolve_upload_mime(safe_name, data.mime_type)

    if not MimeRegistry.is_allowed_mime(resolved_mime):
        raise BadRequestError(
            f"MIME type '{data.mime_type}' is not allowed for upload.",
            code=UploadErrorCode.TYPE_NOT_ALLOWED,
        )

    if data.size <= 0:
        raise BadRequestError(
            "File size must be greater than 0", code=UploadErrorCode.FILE_TOO_LARGE
        )
    _check_per_type_size(resolved_mime, data.size)

    upload_id = str(uuid4())
    quarantine_key = f"quarantine/{user_id}/{upload_id}/{safe_name}"

    await _check_pending_cap(
        user_id,
        redis,
        db,
        privileged=user.role in PRIVILEGED_ROLES,
        reserve_key=quarantine_key,
    )
    await _reserve_storage_limit(data.size, upload_id, redis, db)
    try:
        presigned_url = await generate_presigned_put(
            quarantine_key,
            content_type=resolved_mime,
            ttl=_UPLOAD_INTENT_TTL,
            content_length=data.size,
        )

        intent = json.dumps(
            {
                "user_id": user_id,
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "filename": safe_name,
                "mime_type": resolved_mime,
                "sha256": getattr(data, "sha256", None),
            }
        )
        await redis.set(f"{_UPLOAD_INTENT_PREFIX}{upload_id}", intent, ex=_UPLOAD_INTENT_TTL)

        await _create_upload_row(
            upload_id=upload_id,
            user_id=user_id,
            quarantine_key=quarantine_key,
            filename=safe_name,
            mime_type=resolved_mime,
            size_bytes=data.size,
            db=db,
        )
    except Exception:
        await _release_storage_reservation(upload_id, redis)
        await redis.zrem(f"{_QUOTA_KEY_PREFIX}{user_id}", quarantine_key)
        raise

    return PresignedUploadOut(
        quarantine_key=quarantine_key,
        upload_id=upload_id,
        presigned_url=presigned_url,
        expires_in=_UPLOAD_INTENT_TTL,
    )


# ── POST /api/upload/complete ────────────────────────────────────────────────


@router.post("/complete", response_model=UploadPendingOut, status_code=202)
async def complete_upload(
    data: UploadCompleteRequest,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadPendingOut:
    """Confirm a presigned upload and enqueue background processing."""
    user_id = str(user.id)

    intent_key = f"{_UPLOAD_INTENT_PREFIX}{data.upload_id}"
    async with redis_lock(redis, f"upload-complete:{data.upload_id}", timeout=5, expire=120):
        intent_raw = await redis.get(intent_key)
        if not intent_raw:
            raise BadRequestError(
                "Upload intent not found, expired, or already completed. Please restart the upload.",
                code=UploadErrorCode.INTENT_EXPIRED,
            )

        intent = json.loads(intent_raw)
        if intent["user_id"] != user_id:
            raise ForbiddenError("Upload does not belong to you")
        if intent["quarantine_key"] != data.quarantine_key:
            raise BadRequestError(
                "quarantine_key does not match the initiated upload.",
                code=UploadErrorCode.INTENT_MISMATCH,
            )

        try:
            info = await get_object_info(data.quarantine_key)
        except Exception as exc:
            raise BadRequestError(
                "File not found in storage. Ensure the PUT to the presigned URL succeeded."
            ) from exc

        from app.core.storage.facade import read_object_bytes

        head = await read_object_bytes(data.quarantine_key, byte_count=2048)
        real_mime = guess_mime_from_bytes(head)
        ext = MimeRegistry.get_extension(intent["filename"])
        if real_mime != "application/octet-stream":
            safe_name, ext = _apply_mime_correction(intent["filename"], real_mime, ext)
            intent["filename"] = safe_name
            intent["mime_type"] = real_mime

        _check_per_type_size(intent["mime_type"], info["size"])
        await _reserve_storage_limit(info["size"], data.upload_id, redis, db)
        await redis.zadd(f"{_QUOTA_KEY_PREFIX}{user_id}", {data.quarantine_key: time.time()})
        await _enqueue_processing(
            user_id,
            intent["upload_id"],
            data.quarantine_key,
            intent["filename"],
            intent["mime_type"],
            file_size=info["size"],
            expected_sha256=intent.get("sha256"),
            job_id=f"presigned-process:{intent['upload_id']}",
        )
        await redis.delete(intent_key)

    return UploadPendingOut(
        upload_id=data.upload_id,
        file_key=data.quarantine_key,
        status=UploadStatus.PENDING,
        size=info["size"],
        mime_type=intent["mime_type"],
    )


# ── Presigned Multipart ──────────────────────────────────────────────────────


@router.post("/presigned-multipart/init", response_model=PresignedMultipartInitOut)
async def presigned_multipart_init(
    data: UploadInitRequest,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_uploads)],
) -> PresignedMultipartInitOut:
    """Initialise a direct-to-S3 multipart upload."""
    if not settings.enable_presigned_multipart:
        raise HTTPException(status_code=501, detail="Presigned multipart not enabled")

    user_id = str(user.id)
    safe_name, _ext = _validate_filename(data.filename)
    resolved_mime = MimeRegistry.resolve_upload_mime(safe_name, data.mime_type)

    if not MimeRegistry.is_allowed_mime(resolved_mime):
        raise BadRequestError(
            f"MIME type '{data.mime_type}' is not allowed for upload.",
            code=UploadErrorCode.TYPE_NOT_ALLOWED,
        )

    if data.size < 5 * 1024 * 1024:
        raise BadRequestError("File too small for multipart (min 5 MiB)")
    _check_per_type_size(resolved_mime, data.size)

    upload_id = str(uuid4())
    quarantine_key = f"quarantine/{user_id}/{upload_id}/{safe_name}"

    await _check_pending_cap(
        user_id,
        redis,
        db,
        privileged=user.role in PRIVILEGED_ROLES,
        reserve_key=quarantine_key,
    )
    await _reserve_storage_limit(data.size, upload_id, redis, db)
    s3_multipart_id: str | None = None
    try:
        s3_multipart_id = await create_multipart_upload(
            quarantine_key, content_type=resolved_mime, content_disposition=None
        )

        part_size = _PRESIGNED_MULTIPART_PART_SIZE
        num_parts = (data.size + part_size - 1) // part_size
        parts: list[PresignedMultipartPart] = []

        for i in range(1, num_parts + 1):
            expected_part_size = min(
                part_size,
                data.size - ((i - 1) * part_size),
            )
            url = await generate_presigned_upload_part(
                quarantine_key,
                s3_multipart_id,
                i,
                ttl=_UPLOAD_INTENT_TTL,
                content_length=expected_part_size,
            )
            parts.append(
                PresignedMultipartPart(
                    part_number=i,
                    size=expected_part_size,
                    url=url,
                )
            )

        intent = json.dumps(
            {
                "user_id": user_id,
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "s3_multipart_id": s3_multipart_id,
                "filename": safe_name,
                "mime_type": resolved_mime,
                "size": data.size,
                "part_size": part_size,
                "num_parts": num_parts,
            }
        )
        await redis.set(f"{_UPLOAD_INTENT_PREFIX}{upload_id}", intent, ex=_UPLOAD_INTENT_TTL)

        await _create_upload_row(
            upload_id=upload_id,
            user_id=user_id,
            quarantine_key=quarantine_key,
            filename=safe_name,
            mime_type=resolved_mime,
            size_bytes=data.size,
            db=db,
        )
    except Exception:
        if s3_multipart_id is not None:
            try:
                await abort_multipart_upload(quarantine_key, s3_multipart_id)
            except Exception as abort_exc:
                logger.warning(
                    "Failed to abort multipart upload %s after initialization error: %s",
                    s3_multipart_id,
                    abort_exc,
                )
        await _release_storage_reservation(upload_id, redis)
        await redis.zrem(f"{_QUOTA_KEY_PREFIX}{user_id}", quarantine_key)
        raise

    return PresignedMultipartInitOut(
        quarantine_key=quarantine_key,
        upload_id=upload_id,
        s3_multipart_id=s3_multipart_id,
        parts=parts,
        expires_in=_UPLOAD_INTENT_TTL,
    )


@router.post("/presigned-multipart/complete", response_model=UploadPendingOut, status_code=202)
async def presigned_multipart_complete(
    data: PresignedMultipartCompleteRequest,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadPendingOut:
    """Finalise a presigned multipart upload under the cancellation lock."""
    user_id = str(user.id)
    intent_key = f"{_UPLOAD_INTENT_PREFIX}{data.upload_id}"

    async with redis_lock(redis, f"upload-complete:{data.upload_id}", timeout=5, expire=120):
        intent_raw = await redis.get(intent_key)
        if not intent_raw:
            raise BadRequestError(
                "Upload intent not found, expired, or already completed. Please restart the upload.",
                code=UploadErrorCode.INTENT_EXPIRED,
            )

        intent = json.loads(intent_raw)
        if intent["user_id"] != user_id:
            raise ForbiddenError("You do not own this upload intent")
        await _ensure_presigned_upload_active(intent, redis, db)

        part_manifest = _validated_multipart_manifest(data, intent)

        # Retain a complete tombstone after enqueue. This makes response-loss
        # retries idempotent and keeps cancellation possible after enqueue.
        if intent.get("enqueued"):
            if intent.get("part_manifest") != part_manifest:
                raise BadRequestError(
                    "Multipart completion manifest changed after enqueue.",
                    code=UploadErrorCode.INTENT_MISMATCH,
                )
            return UploadPendingOut(
                upload_id=data.upload_id,
                file_key=intent["quarantine_key"],
                status=UploadStatus.PROCESSING,
                size=int(intent["actual_size"]),
                mime_type=intent["mime_type"],
            )

        if not intent.get("multipart_completed"):
            stored_manifest = intent.get("part_manifest")
            if stored_manifest is not None and stored_manifest != part_manifest:
                raise BadRequestError(
                    "Multipart completion manifest changed during finalization.",
                    code=UploadErrorCode.INTENT_MISMATCH,
                )

            # Checkpoint the exact manifest before the non-atomic S3 operation.
            intent["part_manifest"] = part_manifest
            intent["finalizing"] = True
            await redis.set(intent_key, json.dumps(intent), ex=_UPLOAD_INTENT_TTL)

            try:
                await complete_multipart_verified(
                    intent["quarantine_key"],
                    intent["s3_multipart_id"],
                    part_manifest,
                    expected_size=int(intent["size"]),
                )
            except MultipartCompletionError as exc:
                if exc.retryable:
                    raise ServiceUnavailableError(
                        "Multipart completion status is uncertain. Retry the same completion request."
                    ) from exc
                await _discard_multipart_intent(
                    intent,
                    redis=redis,
                    db=db,
                    reason=exc.detail,
                )
                raise BadRequestError(
                    "Multipart upload could not be completed. Please restart the upload.",
                    code=UploadErrorCode.INTENT_MISMATCH,
                ) from exc

            await _ensure_presigned_upload_active(intent, redis, db)
            intent["multipart_completed"] = True
            intent["finalizing"] = False
            intent["actual_size"] = int(intent["size"])
            await redis.set(intent_key, json.dumps(intent), ex=_UPLOAD_INTENT_TTL)

        from app.core.storage.facade import read_object_bytes

        head = await read_object_bytes(intent["quarantine_key"], byte_count=2048)
        real_mime = guess_mime_from_bytes(head)
        ext = MimeRegistry.get_extension(intent["filename"])
        if real_mime != "application/octet-stream":
            try:
                safe_name, ext = _apply_mime_correction(intent["filename"], real_mime, ext)
            except BadRequestError:
                await _discard_multipart_intent(
                    intent,
                    redis=redis,
                    db=db,
                    reason="Completed multipart object failed authoritative MIME validation",
                )
                raise
            intent["filename"] = safe_name
            intent["mime_type"] = real_mime

        actual_info = await get_object_info(intent["quarantine_key"])
        actual_size = actual_info["size"]
        if actual_size != int(intent["size"]):
            await _discard_multipart_intent(
                intent,
                redis=redis,
                db=db,
                reason="Completed multipart object size did not match upload intent",
            )
            raise BadRequestError(
                "Completed multipart object size does not match the upload intent.",
                code=UploadErrorCode.INTENT_MISMATCH,
            )
        try:
            _check_per_type_size(intent["mime_type"], actual_size)
        except BadRequestError:
            await _discard_multipart_intent(
                intent,
                redis=redis,
                db=db,
                reason="Completed multipart object exceeded its authoritative MIME limit",
            )
            raise

        await _ensure_presigned_upload_active(intent, redis, db)
        await _reserve_storage_limit(actual_size, data.upload_id, redis, db)
        await redis.zadd(
            f"{_QUOTA_KEY_PREFIX}{user_id}",
            {intent["quarantine_key"]: time.time()},
        )
        await _ensure_presigned_upload_active(intent, redis, db)
        await _enqueue_processing(
            user_id=user_id,
            upload_id=intent["upload_id"],
            quarantine_key=intent["quarantine_key"],
            filename=intent["filename"],
            mime_type=intent["mime_type"],
            file_size=actual_size,
            job_id=f"presigned-process:{intent['upload_id']}",
        )

        intent["actual_size"] = actual_size
        intent["enqueued"] = True
        await redis.set(intent_key, json.dumps(intent), ex=_UPLOAD_INTENT_TTL)

    return UploadPendingOut(
        upload_id=data.upload_id,
        file_key=intent["quarantine_key"],
        status=UploadStatus.PROCESSING,
        size=actual_size,
        mime_type=intent["mime_type"],
    )

@router.delete("/presigned-multipart/{upload_id}", status_code=204)
async def presigned_multipart_abort(
    upload_id: str,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Serialize cancellation with completion and retain cleanup retry state."""
    user_id = str(user.id)
    intent_key = f"{_UPLOAD_INTENT_PREFIX}{upload_id}"

    async with redis_lock(redis, f"upload-complete:{upload_id}", timeout=5, expire=120):
        intent_raw = await redis.get(intent_key)
        if not intent_raw:
            return

        intent = json.loads(intent_raw)
        if intent["user_id"] != user_id:
            raise ForbiddenError("You do not own this upload intent")

        # Cancellation is authoritative before any fallible storage cleanup.
        intent["cancelled"] = True
        await redis.set(intent_key, json.dumps(intent), ex=_UPLOAD_INTENT_TTL)
        await db.execute(
            sql_update(Upload)
            .where(Upload.upload_id == intent["upload_id"])
            .values(status="cancelled", error_detail="Aborted by user")
        )
        await db.commit()
        await redis.set(_upload_cancel_key(upload_id), "1", ex=_UPLOAD_CANCEL_TTL)

        cleanup_errors: list[BaseException] = []
        try:
            await abort_multipart_upload(intent["quarantine_key"], intent["s3_multipart_id"])
        except Exception as exc:
            cleanup_errors.append(exc)
        try:
            await delete_object(intent["quarantine_key"])
        except Exception as exc:
            cleanup_errors.append(exc)

        if cleanup_errors:
            logger.warning(
                "Presigned multipart cancellation for %s retained retry state: %s",
                upload_id,
                cleanup_errors[0],
            )
            raise ServiceUnavailableError(
                "Upload was cancelled, but storage cleanup is incomplete. Retry the abort request."
            ) from cleanup_errors[0]

        await redis.delete(intent_key)
        await _release_storage_reservation(upload_id, redis)
        await redis.zrem(f"{_QUOTA_KEY_PREFIX}{user_id}", intent["quarantine_key"])
