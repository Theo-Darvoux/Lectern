"""Presigned upload endpoints: single-part and multipart."""

import json
import logging
import time
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from redis.asyncio import Redis
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import PRIVILEGED_ROLES
from app.core.database import get_db
from app.core.exceptions import BadRequestError, ForbiddenError
from app.core.mimetypes import MimeRegistry, guess_mime_from_bytes
from app.core.redis import get_redis
from app.core.storage import (
    abort_multipart_upload,
    complete_multipart_upload,
    create_multipart_upload,
    generate_presigned_put,
    generate_presigned_upload_part,
    get_object_info,
)
from app.core.typing_ext import RedisProtocol
from app.core.upload_errors import (
    ERR_FILE_TOO_LARGE,
    ERR_INTENT_EXPIRED,
    ERR_INTENT_MISMATCH,
    ERR_TYPE_NOT_ALLOWED,
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


@router.post("/init", response_model=PresignedUploadOut)
async def init_upload(
    data: UploadInitRequest,
    user: CurrentUser,
    redis: Annotated[RedisProtocol, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_uploads)],
    response: Response,
) -> PresignedUploadOut:
    """Request a presigned PUT URL for direct-to-S3 upload."""
    for k, v in _PRESIGNED_DEPRECATION_HEADERS.items():
        response.headers[k] = v
    user_id = str(user.id)

    safe_name, _ext = _validate_filename(data.filename)

    if not MimeRegistry.is_allowed_mime(data.mime_type):
        raise BadRequestError(
            f"MIME type '{data.mime_type}' is not allowed for upload.",
            code=ERR_TYPE_NOT_ALLOWED,
        )

    if data.size <= 0:
        raise BadRequestError("File size must be greater than 0", code=ERR_FILE_TOO_LARGE)
    _check_per_type_size(data.mime_type, data.size)

    upload_id = str(uuid4())
    quarantine_key = f"quarantine/{user_id}/{upload_id}/{safe_name}"

    await _check_pending_cap(
        user_id,
        redis,  # type: ignore[arg-type]
        db,
        privileged=user.role in PRIVILEGED_ROLES,
        reserve_key=quarantine_key,
    )

    presigned_url = await generate_presigned_put(
        quarantine_key,
        content_type=data.mime_type,
        ttl=_UPLOAD_INTENT_TTL,
        content_length=data.size,
    )

    intent = json.dumps(
        {
            "user_id": user_id,
            "upload_id": upload_id,
            "quarantine_key": quarantine_key,
            "filename": safe_name,
            "mime_type": data.mime_type,
            "sha256": getattr(data, "sha256", None),
        }
    )
    await redis.set(f"{_UPLOAD_INTENT_PREFIX}{upload_id}", intent, ex=_UPLOAD_INTENT_TTL)  # type: ignore[call-arg]

    await _create_upload_row(
        upload_id=upload_id,
        user_id=user_id,
        quarantine_key=quarantine_key,
        filename=safe_name,
        mime_type=data.mime_type,
        size_bytes=data.size,
        db=db,
    )

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
    redis: Annotated[RedisProtocol, Depends(get_redis)],
) -> UploadPendingOut:
    """Confirm a presigned upload and enqueue background processing."""
    user_id = str(user.id)

    # Use atomic GETDEL to prevent race conditions where multiple requests
    # attempt to complete the exact same upload intent concurrently.
    res = await redis.execute_command("GETDEL", f"{_UPLOAD_INTENT_PREFIX}{data.upload_id}")
    if not isinstance(res, (str, bytes)) or not res:
        intent_raw: str | None = None
    else:
        intent_raw = res.decode() if isinstance(res, bytes) else res

    if not intent_raw:
        raise BadRequestError(
            "Upload intent not found, expired, or already completed. Please restart the upload.",
            code=ERR_INTENT_EXPIRED,
        )

    intent = json.loads(intent_raw)
    if intent["user_id"] != user_id:
        raise ForbiddenError("Upload does not belong to you")
    if intent["quarantine_key"] != data.quarantine_key:
        raise BadRequestError(
            "quarantine_key does not match the initiated upload.",
            code=ERR_INTENT_MISMATCH,
        )

    try:
        info = await get_object_info(data.quarantine_key)
    except Exception:
        raise BadRequestError(
            "File not found in storage. Ensure the PUT to the presigned URL succeeded."
        )

    # MIME re-validation: Range GET first 2048 bytes
    from app.core.storage import read_object_bytes

    head = await read_object_bytes(data.quarantine_key, byte_count=2048)
    real_mime = guess_mime_from_bytes(head)

    # We run mime correction exactly as we do in direct upload

    ext = MimeRegistry.get_extension(intent["filename"])

    if real_mime != "application/octet-stream":
        safe_name, ext = _apply_mime_correction(intent["filename"], real_mime, ext)
        intent["filename"] = safe_name
        intent["mime_type"] = real_mime

    # Re-validate per-type size limit against actual uploaded size (audit fix #3)
    _check_per_type_size(intent["mime_type"], info["size"])

    await redis.zadd(f"{_QUOTA_KEY_PREFIX}{user_id}", {data.quarantine_key: time.time()})

    await _enqueue_processing(
        user_id,
        intent["upload_id"],
        data.quarantine_key,
        intent["filename"],
        intent["mime_type"],
        file_size=info["size"],
        expected_sha256=intent.get("sha256"),
    )

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

    if not MimeRegistry.is_allowed_mime(data.mime_type):
        raise BadRequestError(
            f"MIME type '{data.mime_type}' is not allowed for upload.",
            code=ERR_TYPE_NOT_ALLOWED,
        )

    if data.size < 5 * 1024 * 1024:
        raise BadRequestError("File too small for multipart (min 5 MiB)")
    _check_per_type_size(data.mime_type, data.size)

    upload_id = str(uuid4())
    quarantine_key = f"quarantine/{user_id}/{upload_id}/{safe_name}"

    await _check_pending_cap(
        user_id,
        redis,
        db,
        privileged=user.role in PRIVILEGED_ROLES,
        reserve_key=quarantine_key,
    )

    s3_multipart_id = await create_multipart_upload(
        quarantine_key, content_type=data.mime_type, content_disposition=None
    )

    part_size = 8 * 1024 * 1024
    num_parts = (data.size + part_size - 1) // part_size
    parts: list[PresignedMultipartPart] = []

    for i in range(1, num_parts + 1):
        url = await generate_presigned_upload_part(
            quarantine_key, s3_multipart_id, i, ttl=_UPLOAD_INTENT_TTL
        )
        parts.append(PresignedMultipartPart(part_number=i, url=url))

    intent = json.dumps(
        {
            "user_id": user_id,
            "upload_id": upload_id,
            "quarantine_key": quarantine_key,
            "s3_multipart_id": s3_multipart_id,
            "filename": safe_name,
            "mime_type": data.mime_type,
            "size": data.size,
        }
    )
    await redis.set(f"{_UPLOAD_INTENT_PREFIX}{upload_id}", intent, ex=_UPLOAD_INTENT_TTL)

    await _create_upload_row(
        upload_id=upload_id,
        user_id=user_id,
        quarantine_key=quarantine_key,
        filename=safe_name,
        mime_type=data.mime_type,
        size_bytes=data.size,
        db=db,
    )

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
    redis: Annotated[RedisProtocol, Depends(get_redis)],
) -> UploadPendingOut:
    """Finalise a presigned multipart upload."""
    user_id = str(user.id)

    # Atomic GETDEL prevents double-completion races (audit review fix)
    res = await redis.execute_command("GETDEL", f"{_UPLOAD_INTENT_PREFIX}{data.upload_id}")
    if not isinstance(res, (str, bytes)) or not res:
        intent_raw: str | None = None
    else:
        intent_raw = res.decode() if isinstance(res, bytes) else res

    if not intent_raw:
        raise BadRequestError(
            "Upload intent not found, expired, or already completed. Please restart the upload.",
            code=ERR_INTENT_EXPIRED,
        )

    intent = json.loads(intent_raw)
    if intent["user_id"] != user_id:
        raise ForbiddenError("You do not own this upload intent")

    await complete_multipart_upload(
        intent["quarantine_key"],
        intent["s3_multipart_id"],
        [p.model_dump() for p in data.parts],
    )

    # MIME re-validation via Range GET (audit fix #4)
    from app.core.storage import read_object_bytes

    head = await read_object_bytes(intent["quarantine_key"], byte_count=2048)
    real_mime = guess_mime_from_bytes(head)
    ext = MimeRegistry.get_extension(intent["filename"])
    if real_mime != "application/octet-stream":
        safe_name, ext = _apply_mime_correction(intent["filename"], real_mime, ext)
        intent["filename"] = safe_name
        intent["mime_type"] = real_mime

    # Validate per-type size limit against ACTUAL S3 size, not client-declared
    # (audit review fix: client can lie about size at init)
    actual_info = await get_object_info(intent["quarantine_key"])
    actual_size = actual_info["size"]
    _check_per_type_size(intent["mime_type"], actual_size)

    await redis.zadd(f"{_QUOTA_KEY_PREFIX}{user_id}", {intent["quarantine_key"]: time.time()})

    await _enqueue_processing(
        user_id=user_id,
        upload_id=intent["upload_id"],
        quarantine_key=intent["quarantine_key"],
        filename=intent["filename"],
        mime_type=intent["mime_type"],
        file_size=actual_size,
    )

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
) -> None:
    """Abort an in-progress multipart upload."""
    user_id = str(user.id)

    intent_raw = await redis.get(f"{_UPLOAD_INTENT_PREFIX}{upload_id}")
    if not intent_raw:
        return

    intent = json.loads(intent_raw)
    if intent["user_id"] != user_id:
        raise ForbiddenError("You do not own this upload intent")

    await abort_multipart_upload(intent["quarantine_key"], intent["s3_multipart_id"])
    await redis.delete(f"{_UPLOAD_INTENT_PREFIX}{upload_id}")

    # Clean up quota and DB row (audit fix #14)
    await redis.zrem(f"{_QUOTA_KEY_PREFIX}{user_id}", intent["quarantine_key"])
    try:
        from app.core.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(
                sql_update(Upload)
                .where(Upload.upload_id == intent["upload_id"])
                .values(status="cancelled", error_detail="Aborted by user")
            )
            await session.commit()
    except Exception as exc:
        logger.warning(
            "Failed to update upload %s to cancelled: %s",
            intent["upload_id"],
            exc,
        )
