"""Authoritative processing status for one uploaded file.

Live progress reaches the browser on the ``uploads`` channel of the master SSE
stream. This endpoint is the source of truth that clients reconcile against.
"""

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import ForbiddenError
from app.core.database.database import get_db
from app.core.database.redis import get_redis
from app.dependencies.auth import CurrentUser
from app.models.upload import Upload
from app.routers.upload.helpers import _STATUS_CACHE_PREFIX
from app.schemas.material import UploadStatus, UploadStatusOut

router = APIRouter()


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


async def _load_authoritative_upload(
    file_key: str, user_id: uuid.UUID, db: AsyncSession
) -> Upload | None:
    rows = list(
        (
            await db.scalars(
                select(Upload)
                .where(
                    Upload.user_id == user_id,
                    (Upload.final_key == file_key) | (Upload.quarantine_key == file_key),
                )
                .order_by(Upload.updated_at.desc())
            )
        ).all()
    )
    if not rows and "/" in file_key:
        parts = file_key.split("/")
        if len(parts) >= 3:
            row = await db.scalar(
                select(Upload).where(Upload.upload_id == parts[2], Upload.user_id == user_id)
            )
            if row is not None:
                rows.append(row)
    if not rows:
        return None
    applied = next((row for row in rows if row.status == "applied"), None)
    if applied is not None:
        return applied
    return next(
        (
            row
            for row in rows
            if row.status == "clean"
            and (not (row.final_key or "").startswith("cas/") or int(row.cas_ref_count or 0) > 0)
        ),
        rows[0],
    )


def _authoritative_terminal_payload(row: Upload, file_key: str) -> dict[str, Any] | None:
    status = row.status
    uses_cas = bool(row.final_key and row.final_key.startswith("cas/"))
    has_active_owner = not uses_cas or int(row.cas_ref_count or 0) > 0
    if status == "cancelled" or (status == "clean" and not has_active_owner):
        status = "failed"
    if status not in ("clean", "failed", "malicious", "applied"):
        return None
    has_result = status == "applied" or (status == "clean" and has_active_owner)
    return {
        "upload_id": row.upload_id,
        "file_key": file_key,
        "status": status,
        "detail": row.error_detail or ("Success" if has_result else "Upload is no longer active"),
        "result": {
            "file_key": row.final_key or file_key,
            "size": row.size_bytes,
            "original_size": row.size_bytes,
            "mime_type": row.mime_type,
            "file_name": row.filename,
        }
        if has_result
        else None,
    }


@router.get("/status/{file_key:path}", response_model=UploadStatusOut)
async def upload_status(
    file_key: str,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadStatusOut:
    """Return upload status with the database overriding stale terminal cache state."""
    await _check_file_ownership(file_key, str(user.id), db)

    row = await _load_authoritative_upload(file_key, user.id, db)
    authoritative = _authoritative_terminal_payload(row, file_key) if row else None
    cached = await redis.get(f"{_STATUS_CACHE_PREFIX}{file_key}")
    try:
        cached_payload = json.loads(cached) if cached else None
    except ValueError:
        cached_payload = None

    if authoritative is not None:
        # The row stores only the message; the structured code lives in the event.
        error_code = cached_payload.get("error_code") if cached_payload else None
        return UploadStatusOut(**authoritative, error_code=error_code)

    if not cached_payload:
        return UploadStatusOut(file_key=file_key, status=UploadStatus.PENDING)
    try:
        return UploadStatusOut(**cached_payload)
    except Exception:
        return UploadStatusOut(file_key=file_key, status=UploadStatus.PENDING)
