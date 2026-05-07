"""POST /api/upload -- direct file upload to quarantine."""

import logging
import mimetypes
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, UploadFile
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import MAGIC_HEADER_SIZE, PRIVILEGED_ROLES
from app.core.database import get_db
from app.core.exceptions import BadRequestError
from app.core.file_security import SvgSecurityError
from app.core.mimetypes import guess_mime_from_bytes
from app.core.processing import ProcessingFile
from app.core.redis import get_redis
from app.core.storage import get_s3_client
from app.core.upload_errors import ERR_SVG_UNSAFE
from app.dependencies.auth import CurrentUser
from app.dependencies.rate_limit import rate_limit_uploads
from app.routers.upload.helpers import (
    _IDEM_KEY_PREFIX,
    _IDEM_TTL,
    _check_pending_cap,
    _create_upload_row,
    _enqueue_processing,
)
from app.routers.upload.validators import (
    _apply_mime_correction,
    _check_per_type_size,
    _validate_filename,
)
from app.schemas.material import UploadPendingOut, UploadStatus
from app.services.auth import get_full_auth_config

logger = logging.getLogger("wikint")

router = APIRouter()


@router.post("", response_model=UploadPendingOut, status_code=202)
async def upload_file(
    file: UploadFile,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    _: Annotated[None, Depends(rate_limit_uploads)],
) -> UploadPendingOut:
    """Direct upload: stream file to quarantine, enqueue async security processing.

    Returns immediately (202 Accepted) with the quarantine key.
    The client should open GET /events/{file_key} to track processing status.

    Every upload goes through the full async security pipeline — there is no
    CAS fast-path here by design.  Deduplication happens in the background
    worker after scanning, not at upload time.
    """
    user_id = str(user.id)
    upload_id = str(uuid4())

    # Validate X-Upload-ID early (before streaming)
    idem_header = request.headers.get("X-Upload-ID")
    idem_cache_key: str | None = None
    if idem_header:
        try:
            UUID(idem_header)
        except ValueError:
            raise BadRequestError("X-Upload-ID must be a valid UUID")
        upload_id = idem_header

    # Fetch dynamic config
    config = await get_full_auth_config(db, redis)

    # Process allowed lists
    allowed_exts: set[str] | None = None
    if config.get("allowed_extensions"):
        allowed_exts = {
            e.strip().lower() for e in config["allowed_extensions"].split(",") if e.strip()
        }
        if not all(e.startswith(".") for e in allowed_exts):
            # Ensure dots
            allowed_exts = {e if e.startswith(".") else f".{e}" for e in allowed_exts}

    allowed_mimes: set[str] | None = None
    if config.get("allowed_mime_types"):
        allowed_mimes = {
            m.strip().lower() for m in config["allowed_mime_types"].split(",") if m.strip()
        }

    # Validate filename / extension
    safe_name, ext = _validate_filename(file.filename or "unnamed", allowed_extensions=allowed_exts)

    # Stream to a temp file (no full-body read into RAM)
    max_bytes = (
        (  # type: ignore[operator]
            config.get("max_file_size_mb")
            if config.get("max_file_size_mb") is not None
            else settings.max_file_size_mb
        )
        * 1024
        * 1024
    )
    pf = await ProcessingFile.from_upload(file, max_bytes)

    try:
        # MIME detection from first MAGIC_HEADER_SIZE bytes only
        with pf.open("rb") as fh:
            head = fh.read(MAGIC_HEADER_SIZE)

        # Content-aware idempotency (X-Upload-ID path)
        if idem_header:
            idem_cache_key = f"{_IDEM_KEY_PREFIX}{idem_header}"
            if cached := await redis.get(idem_cache_key):
                return UploadPendingOut.model_validate_json(cached)

        real_mime = guess_mime_from_bytes(head)

        if real_mime != "application/octet-stream":
            safe_name, ext = _apply_mime_correction(
                safe_name, real_mime, ext, allowed_mimes=allowed_mimes
            )

        mime_type: str = real_mime
        if mime_type == "application/octet-stream":
            guessed, _enc = mimetypes.guess_type(safe_name)
            mime_type = guessed or "application/octet-stream"

        _check_per_type_size(mime_type, pf.size, config=config)

        from app.routers.upload.helpers import _check_storage_limit

        await _check_storage_limit(pf.size, config=config)

        # SVG safety check
        if mime_type == "image/svg+xml":
            try:
                from app.core.file_security import check_svg_safety_stream

                with pf.open("rb") as fh:
                    check_svg_safety_stream(fh, safe_name)
            except SvgSecurityError as exc:
                raise BadRequestError(str(exc), code=ERR_SVG_UNSAFE) from exc

        file_sha256 = await pf.sha256()

        if not idem_cache_key:
            # Content-aware idempotency check (runs regardless of X-Upload-ID)
            idem_cache_key = f"{_IDEM_KEY_PREFIX}{user_id}:{upload_id}:{file_sha256}"
            if cached := await redis.get(idem_cache_key):
                return UploadPendingOut.model_validate_json(cached)

        quarantine_key = f"quarantine/{user_id}/{upload_id}/{safe_name}"

        # Pending upload cap
        await _check_pending_cap(
            user_id,
            redis,
            privileged=user.role in PRIVILEGED_ROLES,
            reserve_key=quarantine_key,
        )

        # Stream file to quarantine
        async with get_s3_client() as s3:
            await s3.upload_file(  # type: ignore[call-arg]
                Filename=str(pf.path),
                Bucket=config.get("s3_bucket") or settings.s3_bucket,
                Key=quarantine_key,
                ExtraArgs={"ContentType": mime_type},
            )

        await _create_upload_row(
            upload_id=upload_id,
            user_id=user_id,
            quarantine_key=quarantine_key,
            filename=safe_name,
            mime_type=mime_type,
            size_bytes=pf.size,
        )

        await _enqueue_processing(
            user_id, upload_id, quarantine_key, safe_name, mime_type, file_size=pf.size
        )

        result = UploadPendingOut(
            upload_id=upload_id,
            file_key=quarantine_key,
            status=UploadStatus.PENDING,
            size=pf.size,
            mime_type=mime_type,
        )

        if idem_cache_key:
            await redis.set(idem_cache_key, result.model_dump_json(), ex=_IDEM_TTL)

        return result

    finally:
        pf.cleanup()
