"""Upload status endpoints: config, check-exists, batch-status, history, cancel."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.exceptions import BadRequestError, ForbiddenError
from app.core.common.upload_limits import upload_size_limit
from app.core.database.database import get_db
from app.core.database.redis import get_redis, redis_lock
from app.core.media.mimetypes import ALLOWED_EXTENSIONS, ALLOWED_MIME_TYPES
from app.core.security.cas import hmac_cas_key
from app.core.storage.facade import delete_object, generate_presigned_get
from app.dependencies.auth import CurrentUser
from app.models.upload import Upload
from app.routers.upload.cancellation import (
    cancel_upload_lifecycle,
    upload_lifecycle_lock_name,
)
from app.routers.upload.helpers import (
    _STATUS_CACHE_PREFIX,
    _release_storage_reservation,
)
from app.schemas.material import (
    BatchStatusRequest,
    CheckExistsOut,
    CheckExistsRequest,
    UploadHistoryItem,
    UploadHistoryOut,
    UploadStatus,
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


@router.get("/config", response_model=UploadConfigOut)
async def get_upload_config() -> UploadConfigOut:
    """Return the current upload configuration (allowed types, size limits, recommended path).

    Clients should use ``recommended_path`` and ``direct_threshold_mb`` to decide
    which upload path to use without hard-coding the thresholds.
    """
    allowed_exts = ALLOWED_EXTENSIONS
    if settings.allowed_extensions:
        allowed_exts = [e.strip() for e in settings.allowed_extensions.split(",") if e.strip()]  # type: ignore[assignment]

    allowed_mimes = ALLOWED_MIME_TYPES
    if settings.allowed_mime_types:
        allowed_mimes = [m.strip() for m in settings.allowed_mime_types.split(",") if m.strip()]  # type: ignore[assignment]

    return UploadConfigOut(
        allowed_extensions=sorted(allowed_exts),
        allowed_mimetypes=sorted(allowed_mimes),
        max_file_size_mb=settings.max_file_size_mb,
        max_size_mb_by_mime={
            mime: upload_size_limit(mime)[0] // (1024 * 1024) for mime in allowed_mimes
        },
        recommended_path="direct",
        direct_threshold_mb=settings.direct_upload_threshold_mb,
    )


# ── DELETE /api/upload/{upload_id} ───────────────────────────────────────────


@router.delete("/{upload_id}", status_code=204)
async def cancel_upload(
    upload_id: str,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
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


# ── POST /api/upload/check-exists ────────────────────────────────────────────


@router.post("/check-exists", response_model=CheckExistsOut)
async def check_file_exists(
    data: CheckExistsRequest,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CheckExistsOut:
    """Check whether an identical file (by SHA-256) has already been processed."""
    user_id = str(user.id)

    sha256_cache_key = f"upload:sha256:{user_id}:{data.sha256}"
    cached = await redis.get(sha256_cache_key)
    if cached:
        file_key = cached.decode() if isinstance(cached, bytes) else str(cached)
        cached_owner = await db.scalar(
            select(Upload.id).where(
                Upload.user_id == user.id,
                Upload.sha256 == data.sha256,
                Upload.final_key == file_key,
                Upload.status == "clean",
                Upload.cas_ref_count > 0,
            )
        )
        if cached_owner is not None:
            from app.core.storage.facade import object_exists

            if await object_exists(file_key):
                return CheckExistsOut(exists=True, file_key=file_key)
        await redis.delete(sha256_cache_key)

    # ── Global CAS fallback (Audit Fix #15) ──
    # Return exists=True but WITHOUT a raw cas/ key to avoid leaking
    # internal storage paths.  The upload flow's CAS-hit path will handle
    # the actual copy from CAS to the per-user prefix.
    cas_key = hmac_cas_key(data.sha256)
    cas_raw = await redis.get(cas_key)
    if cas_raw:
        cas_data = json.loads(cas_raw)
        file_key = cas_data.get("final_key")
        if file_key:
            from app.core.storage.facade import object_exists

            if await object_exists(file_key):
                # A global CAS hit is only a hint. The upload flow must acquire
                # new ownership before any user-scoped cache points at the object.
                return CheckExistsOut(exists=True, file_key=None)

    return CheckExistsOut(exists=False, file_key=None)


# ── POST /api/upload/status/batch ────────────────────────────────────────────


@router.post("/status/batch")
async def batch_upload_status(
    data: BatchStatusRequest,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> dict[str, Any]:
    """Poll the processing status for up to 50 file keys in a single request."""
    user_id_str = str(user.id)

    # Filter keys to only those belonging to the user.
    # V1 keys: quarantine/{user_id}/... or uploads/{user_id}/...
    # V2 keys: cas/{hmac} (ownership verified via Upload table below)
    owned_keys: list[str] = []
    cas_keys_to_verify: list[str] = []
    for fk in data.file_keys:
        fk_str = str(fk)
        if fk_str.startswith(f"quarantine/{user_id_str}/") or fk_str.startswith(
            f"uploads/{user_id_str}/"
        ):
            owned_keys.append(fk_str)
        elif fk_str.startswith("cas/"):
            cas_keys_to_verify.append(fk_str)

    # Verify CAS key ownership via Upload table
    if cas_keys_to_verify:
        from app.core.database.database import async_session_factory

        async with async_session_factory() as _db:
            verified = set(
                await _db.scalars(
                    select(Upload.final_key).where(
                        Upload.final_key.in_(cas_keys_to_verify),
                        Upload.user_id == user.id,
                        Upload.status == "clean",
                        Upload.cas_ref_count > 0,
                    )
                )
            )
        owned_keys.extend(k for k in cas_keys_to_verify if k in verified)

    results: dict[str, dict] = {}  # type: ignore[type-arg]
    if not owned_keys:
        return {"statuses": results}

    # Fetch statuses from Redis
    cache_keys = [f"{_STATUS_CACHE_PREFIX}{k}" for k in owned_keys]
    values = await redis.mget(*cache_keys)

    # Always load authoritative rows for the requested keys. Redis is a
    # presentation cache and must not resurrect a database-cancelled upload.
    fallback_data: dict[str, dict[str, Any]] = {}
    authoritative_rows: dict[str, Upload] = {}
    from app.core.database.database import async_session_factory

    async with async_session_factory() as _db:
        db_res = await _db.execute(
            select(Upload)
            .where(
                Upload.user_id == user.id,
                (Upload.final_key.in_(owned_keys)) | (Upload.quarantine_key.in_(owned_keys)),
            )
            .order_by(Upload.created_at.desc())
        )
        for row in db_res.scalars().all():
            for key in (row.final_key, row.quarantine_key):
                if key in owned_keys:
                    authoritative_rows.setdefault(key, row)
            has_active_cas_ref = int(row.cas_ref_count or 0) > 0
            response_status = row.status
            if response_status == "cancelled" or (
                response_status == "clean" and not has_active_cas_ref
            ):
                response_status = "failed"
            for key in (row.final_key, row.quarantine_key):
                if key in owned_keys and row.status in (
                    "clean",
                    "failed",
                    "malicious",
                    "cancelled",
                    "applied",
                ):
                    fallback_data[key] = {
                        "upload_id": row.upload_id,
                        "file_key": key,
                        "status": response_status,
                        "detail": row.error_detail
                        or (
                            "Success"
                            if row.status == "clean" and has_active_cas_ref
                            else "Upload is no longer active"
                        ),
                        "result": {
                            "file_key": row.final_key or key,
                            "size": row.size_bytes,
                            "original_size": row.size_bytes,
                            "mime_type": row.mime_type,
                            "file_name": row.filename,
                        }
                        if row.status == "clean" and has_active_cas_ref
                        else None,
                        "overall_percent": 1.0,
                    }

    for file_key, cached in zip(owned_keys, values, strict=False):
        if cached:
            try:
                cached_data = json.loads(cached)
                authoritative_row = authoritative_rows.get(file_key)
                if (
                    cached_data.get("status") == "clean"
                    and authoritative_row is not None
                    and (
                        authoritative_row.status != "clean"
                        or int(authoritative_row.cas_ref_count or 0) <= 0
                    )
                ):
                    await redis.delete(f"{_STATUS_CACHE_PREFIX}{file_key}")
                    results[file_key] = fallback_data.get(file_key) or {
                        "file_key": file_key,
                        "status": UploadStatus.PENDING,
                    }
                    continue
                # Apply fallback fields if needed
                if cached_data.get("status") == "clean" and cached_data.get("result"):
                    if not cached_data["result"].get("file_name") or not cached_data["result"].get(
                        "original_size"
                    ):
                        fb_entry = fallback_data.get(file_key)
                        fb = fb_entry.get("result") if fb_entry else None
                        if fb:
                            if not cached_data["result"].get("file_name"):
                                cached_data["result"]["file_name"] = fb["file_name"]
                            if not cached_data["result"].get("original_size"):
                                cached_data["result"]["original_size"] = fb["original_size"]
                results[file_key] = cached_data
            except Exception:
                results[file_key] = fallback_data.get(file_key) or {
                    "file_key": file_key,
                    "status": UploadStatus.PENDING,
                }
        else:
            results[file_key] = fallback_data.get(file_key) or {
                "file_key": file_key,
                "status": UploadStatus.PENDING,
            }

    return {"statuses": results}


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


@router.post("/request-url", deprecated=True)
async def request_upload_url_deprecated() -> None:
    raise BadRequestError(
        "This endpoint has been removed. Use POST /api/upload/init for presigned uploads "
        "or POST /api/upload for direct uploads."
    )
