"""Authoritative processing status for one uploaded file.

Live progress reaches the browser on the ``uploads`` channel of the master SSE
stream. This endpoint is the source of truth that clients reconcile against:
the database decides terminal states, and the Redis cache only supplies live
progress for uploads still in flight.
"""

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import ForbiddenError
from app.core.common.upload_keys import UPLOAD_STATUS_PREFIX
from app.core.database.database import get_db
from app.core.database.redis import RedisClient, get_redis
from app.dependencies.auth import CurrentUser
from app.models.upload import Upload
from app.schemas.material import UploadCompleteOut, UploadStatus, UploadStatusOut

router = APIRouter()

_TERMINAL_STATUSES = {"clean", "failed", "malicious", "applied", "cancelled"}


def _backs_a_file(row: Upload) -> bool:
    """A CAS upload whose last reference was released no longer backs a file."""
    return not (row.final_key or "").startswith("cas/") or (row.cas_ref_count or 0) > 0


async def _load_upload(
    file_key: str, user_id: uuid.UUID, db: AsyncSession, *, own_prefix: bool
) -> Upload | None:
    """Return the user's upload row that best describes ``file_key``.

    Several rows can share a CAS key. An applied row wins, then a clean row that
    still backs a file, then the most recently updated row.
    """
    rows = list(
        await db.scalars(
            select(Upload)
            .where(
                Upload.user_id == user_id,
                (Upload.final_key == file_key) | (Upload.quarantine_key == file_key),
            )
            .order_by(Upload.updated_at.desc())
        )
    )
    if not rows and own_prefix:
        # quarantine/{user_id}/{upload_id}/...: the row may still hold a provisional key.
        upload_id = file_key.split("/")[2] if file_key.count("/") >= 2 else None
        row = await db.scalar(
            select(Upload).where(Upload.upload_id == upload_id, Upload.user_id == user_id)
        )
        rows = [row] if row is not None else []
    if not rows:
        return None
    return (
        next((r for r in rows if r.status == "applied"), None)
        or next((r for r in rows if r.status == "clean" and _backs_a_file(r)), None)
        or rows[0]
    )


def _terminal_status(row: Upload, file_key: str, error_code: str | None) -> UploadStatusOut:
    has_result = row.status == "applied" or (row.status == "clean" and _backs_a_file(row))
    status = row.status if row.status in ("applied", "malicious") or has_result else "failed"
    return UploadStatusOut(
        upload_id=row.upload_id,
        file_key=file_key,
        status=UploadStatus(status),
        detail=row.error_detail or ("Success" if has_result else "Upload is no longer active"),
        result=UploadCompleteOut(
            file_key=row.final_key or file_key,
            size=row.size_bytes or 0,
            original_size=row.size_bytes or 0,
            mime_type=row.mime_type or "application/octet-stream",
            file_name=row.filename,
        )
        if has_result
        else None,
        error_code=error_code,
    )


async def _cached_event(redis: RedisClient, file_key: str) -> dict[str, Any] | None:
    raw = await redis.get(f"{UPLOAD_STATUS_PREFIX}{file_key}")
    try:
        cached = json.loads(raw) if raw else None
    except ValueError:
        return None
    return cached if isinstance(cached, dict) else None


@router.get("/status/{file_key:path}", response_model=UploadStatusOut)
async def upload_status(
    file_key: str,
    user: CurrentUser,
    redis: Annotated[RedisClient, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadStatusOut:
    """Return upload status with the database overriding stale terminal cache state."""
    own_prefix = file_key.startswith((f"quarantine/{user.id}/", f"uploads/{user.id}/"))
    row = await _load_upload(file_key, user.id, db, own_prefix=own_prefix)
    # A CAS key is shared by identical content, so only an upload row proves ownership.
    if row is None and not own_prefix:
        raise ForbiddenError("File does not belong to you")

    cached = await _cached_event(redis, file_key)
    if row is not None and row.status in _TERMINAL_STATUSES:
        # The row stores only the message; the structured code lives in the event.
        return _terminal_status(row, file_key, cached.get("error_code") if cached else None)

    if cached is not None:
        try:
            return UploadStatusOut(**cached)
        except (ValidationError, TypeError):
            pass
    return UploadStatusOut(file_key=file_key, status=UploadStatus.PENDING)
