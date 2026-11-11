"""Upload endpoints: config, history, cancel."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.batch_upload_limits import (
    BATCH_MAX_FILES,
    BATCH_MAX_FILES_PRIVILEGED,
    BATCH_MAX_PATH_DEPTH,
    BATCH_MAX_TOTAL_EXTRACTED_BYTES,
    BATCH_MAX_ZIP_SIZE_BYTES,
)
from app.core.common.exceptions import BadRequestError, ForbiddenError
from app.core.common.upload_limits import (
    configured_allowed_extensions,
    configured_allowed_mimes,
    upload_size_limit,
)
from app.core.database.database import get_db
from app.core.database.redis import RedisClient, get_redis, redis_lock
from app.core.media.mimetypes import ALLOWED_EXTENSIONS, ALLOWED_MIME_TYPES
from app.core.storage.facade import delete_object, generate_presigned_get
from app.dependencies.auth import CurrentUser
from app.models.upload import Upload
from app.routers.upload.cancellation import (
    cancel_upload_lifecycle,
    upload_lifecycle_lock_name,
)
from app.routers.upload.helpers import (
    _release_storage_reservation,
)
from app.schemas.material import (
    UploadHistoryItem,
    UploadHistoryOut,
)

router = APIRouter()


# ── GET /api/upload/config ───────────────────────────────────────────────────


class UploadConfigOut(BaseModel):
    allowed_extensions: list[str]
    allowed_mimetypes: list[str]
    max_file_size_mb: int
    max_size_mb_by_mime: dict[str, int]
    recommended_path: str  # "direct" | "tus"
    direct_threshold_mb: int  # files below this size → use direct path
    batch_max_zip_size_bytes: int
    batch_max_total_extracted_bytes: int
    batch_max_files: int
    batch_max_files_privileged: int
    batch_max_path_depth: int


@router.get("/config", response_model=UploadConfigOut)
async def get_upload_config() -> UploadConfigOut:
    """Return the current upload configuration (allowed types, size limits, recommended path).

    Clients should use ``recommended_path`` and ``direct_threshold_mb`` to decide
    which upload path to use without hard-coding the thresholds.
    """
    allowed_exts = configured_allowed_extensions() or ALLOWED_EXTENSIONS
    allowed_mimes = configured_allowed_mimes() or ALLOWED_MIME_TYPES

    return UploadConfigOut(
        allowed_extensions=sorted(allowed_exts),
        allowed_mimetypes=sorted(allowed_mimes),
        max_file_size_mb=settings.max_file_size_mb,
        max_size_mb_by_mime={
            mime: upload_size_limit(mime)[0] // (1024 * 1024) for mime in allowed_mimes
        },
        recommended_path="direct",
        direct_threshold_mb=settings.direct_upload_threshold_mb,
        batch_max_zip_size_bytes=BATCH_MAX_ZIP_SIZE_BYTES,
        batch_max_total_extracted_bytes=BATCH_MAX_TOTAL_EXTRACTED_BYTES,
        batch_max_files=BATCH_MAX_FILES,
        batch_max_files_privileged=BATCH_MAX_FILES_PRIVILEGED,
        batch_max_path_depth=BATCH_MAX_PATH_DEPTH,
    )


# ── DELETE /api/upload/{upload_id} ───────────────────────────────────────────


@router.delete("/{upload_id}", status_code=204)
async def cancel_upload(
    upload_id: str,
    user: CurrentUser,
    redis: Annotated[RedisClient, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Cancel any upload through the shared CAS-aware lifecycle operation."""
    async with redis_lock(
        redis,
        upload_lifecycle_lock_name(upload_id),
        timeout=120.0,
        expire=300.0,
    ):
        await cancel_upload_lifecycle(
            upload_id=upload_id,
            user_id=str(user.id),
            redis=redis,
            db=db,
            reason="Cancelled by user",
            delete_object_fn=delete_object,
            release_reservation_fn=_release_storage_reservation,
        )


# ── GET /api/upload/mine ─────────────────────────────────────────────────────


@router.get("/mine", response_model=UploadHistoryOut)
async def list_my_uploads(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> UploadHistoryOut:
    """Return the authenticated user's paginated upload history.

    Results are ordered by creation time descending (most recent first).
    All statuses are included (pending, processing, clean, failed, malicious).
    """
    total = (
        await db.scalar(select(func.count()).select_from(Upload).where(Upload.user_id == user.id))
        or 0
    )

    result = await db.execute(
        select(Upload)
        .where(Upload.user_id == user.id)
        .order_by(Upload.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = result.scalars().all()

    return UploadHistoryOut(
        items=[
            UploadHistoryItem(
                upload_id=row.upload_id,
                filename=row.filename,
                mime_type=row.mime_type,
                size_bytes=row.size_bytes,
                status=row.status,
                sha256=row.sha256,
                final_key=row.final_key,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ],
        total=total,
        page=page,
        pages=max(1, (total + limit - 1) // limit),
    )


# ── GET /api/upload/preview ──────────────────────────────────────────────────


@router.get("/preview")
async def get_upload_preview(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file_key: str = Query(...),
) -> dict[str, str]:
    """Retrieve a temporary presigned GET URL for an uploaded file in staging."""
    user_id_str = str(user.id)

    # Verify ownership
    if file_key.startswith("cas/"):
        row = await db.scalar(
            select(Upload.id).where(
                Upload.final_key == file_key, Upload.user_id == user.id, Upload.status == "clean"
            )
        )
        if not row:
            raise BadRequestError("File could not be found or verified.")
    elif file_key.startswith(f"quarantine/{user_id_str}/") or file_key.startswith(
        f"uploads/{user_id_str}/"
    ):
        pass  # Owned by user namespace
    else:
        raise ForbiddenError("You are not authorized to preview this file.")

    # Refuse to serve unscanned quarantine files
    if file_key.startswith("quarantine/"):
        raise BadRequestError("File is still being processed and cannot be previewed yet.")

    # Try looking up filename and mimetype in the DB
    upload_row = await db.scalar(
        select(Upload).where(Upload.final_key == file_key, Upload.user_id == user.id)
    )

    filename = upload_row.filename if upload_row else None
    content_type = upload_row.mime_type if upload_row else None

    # Generate the URL
    url = await generate_presigned_get(file_key, filename=filename, content_type=content_type)

    return {"url": url}


# ── Deprecated stubs ─────────────────────────────────────────────────────────
