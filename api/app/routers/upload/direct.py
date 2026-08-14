"""POST /api/upload -- direct file upload to quarantine."""

import contextlib
import hashlib
import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, UploadFile
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.constants import MAGIC_HEADER_SIZE, PRIVILEGED_ROLES
from app.core.common.exceptions import BadRequestError, ForbiddenError
from app.core.common.upload_errors import UploadErrorCode
from app.core.database.database import get_db
from app.core.database.post_commit import register_transaction_callbacks
from app.core.database.redis import get_redis
from app.core.events.processing import ProcessingFile
from app.core.media.mimetypes import MimeRegistry, guess_mime_from_bytes
from app.core.security.file_security import SvgSecurityError, check_svg_safety_stream
from app.core.storage.facade import delete_object, get_s3_client
from app.dependencies.auth import CurrentUser
from app.dependencies.rate_limit import rate_limit_uploads
from app.models.upload import Upload
from app.routers.upload.helpers import (
    _IDEM_KEY_PREFIX,
    _IDEM_TTL,
    _check_pending_cap,
    _create_upload_row,
    _queue_processing_after_commit,
    _release_storage_reservation,
    _reserve_storage_limit,
)
from app.routers.upload.validators import (
    _apply_mime_correction,
    _check_per_type_size,
    _validate_filename,
)
from app.schemas.material import UploadPendingOut, UploadStatus

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authoritative_upload_result(
    db: AsyncSession,
    user_id: str,
    upload_id: str,
) -> UploadPendingOut | None:
    existing = await db.scalar(select(Upload).where(Upload.upload_id == upload_id))
    if existing is None:
        return None
    if str(existing.user_id) != user_id:
        raise ForbiddenError("X-Upload-ID is already owned by another user")
    existing_key = existing.final_key or existing.quarantine_key
    if existing_key is None:
        raise BadRequestError("Existing upload has no storage key")
    return UploadPendingOut(
        upload_id=existing.upload_id,
        file_key=existing_key,
        status=UploadStatus(existing.status),
        size=existing.size_bytes or 0,
        mime_type=existing.mime_type or "application/octet-stream",
    )


def _upload_advisory_lock_key(upload_id: str) -> int:
    """Map a globally unique Upload.upload_id onto PostgreSQL's signed bigint lock key."""
    digest = hashlib.blake2b(
        upload_id.encode(),
        digest_size=8,
        person=b"lectern-upload",
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


async def _claim_direct_upload_idempotency(
    db: AsyncSession,
    user_id: str,
    upload_id: str,
) -> UploadPendingOut | None:
    """Serialize one X-Upload-ID before any shared Redis/S3 side effect.

    The lock is transaction-scoped, so process death, rollback, and successful
    COMMIT all release ownership automatically. Upload.upload_id is globally
    unique, therefore the advisory key is global too rather than tenant-scoped.
    After the lock is acquired we re-read PostgreSQL at READ COMMITTED; a waiter
    sees the winner's committed row and returns without touching its resources.
    """
    bind = db.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _upload_advisory_lock_key(upload_id)},
        )
    elif dialect != "sqlite":
        raise RuntimeError(f"Unsupported database dialect for upload idempotency: {dialect}")
    return await _authoritative_upload_result(db, user_id, upload_id)


async def _validated_idempotency_cache_hit(
    redis: Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    cache_key: str,
    user_id: str,
    expected_upload_id: str,
) -> UploadPendingOut | None:
    """Treat Redis as a cache only; PostgreSQL remains authoritative."""
    cached = await redis.get(cache_key)
    if not cached:
        return None
    try:
        cached_result = UploadPendingOut.model_validate_json(cached)
    except Exception:
        await redis.delete(cache_key)
        return None
    if cached_result.upload_id != expected_upload_id:
        await redis.delete(cache_key)
        return None

    authoritative = await _authoritative_upload_result(db, user_id, expected_upload_id)
    if authoritative is None:
        # A stale result from a failed/ambiguous pre-fix COMMIT must never certify
        # a new success. Drop the optimization and continue from PostgreSQL truth.
        await redis.delete(cache_key)
        return None
    return authoritative


def _cache_idempotency_after_commit(
    db: AsyncSession,
    redis: Redis,  # type: ignore[type-arg]
    cache_key: str,
    result: UploadPendingOut,
) -> bool:
    """Publish Redis idempotency state only after the Upload/outbox COMMIT."""
    payload = result.model_dump_json()

    async def _on_commit() -> None:
        await redis.set(cache_key, payload, ex=_IDEM_TTL)

    async def _on_rollback() -> None:
        # Defensive cleanup for a stale cache entry produced by older releases.
        await redis.delete(cache_key)

    return register_transaction_callbacks(
        db,
        on_rollback=_on_rollback,
        on_commit=_on_commit,
    )


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
            idem_header = str(UUID(idem_header))
        except ValueError:
            raise BadRequestError("X-Upload-ID must be a valid UUID")
        upload_id = idem_header

    # Process allowed lists
    allowed_exts: set[str] | None = None
    if settings.allowed_extensions:
        allowed_exts = {
            e.strip().lower() for e in settings.allowed_extensions.split(",") if e.strip()
        }
        if not all(e.startswith(".") for e in allowed_exts):
            # Ensure dots
            allowed_exts = {e if e.startswith(".") else f".{e}" for e in allowed_exts}

    allowed_mimes: set[str] | None = None
    if settings.allowed_mime_types:
        allowed_mimes = {
            m.strip().lower() for m in settings.allowed_mime_types.split(",") if m.strip()
        }

    # Validate filename / extension
    safe_name, ext = _validate_filename(file.filename or "unnamed", allowed_extensions=allowed_exts)

    # Stream to a temp file (no full-body read into RAM)
    max_bytes = settings.max_file_size_mb * 1024 * 1024  # type: ignore[operator]
    pf = await ProcessingFile.from_upload(file, max_bytes)
    quarantine_key: str | None = None
    quota_reserved = False
    storage_reserved = False
    object_uploaded = False

    try:
        # MIME detection from first MAGIC_HEADER_SIZE bytes only
        with pf.open("rb") as fh:
            head = fh.read(MAGIC_HEADER_SIZE)

        # Content-aware idempotency (X-Upload-ID path)
        if idem_header:
            # Idempotency keys are caller-controlled and must never cross tenant
            # boundaries, even when two users deliberately choose the same UUID.
            idem_cache_key = f"{_IDEM_KEY_PREFIX}{user_id}:{idem_header}"
            if cached_result := await _validated_idempotency_cache_hit(
                redis, db, idem_cache_key, user_id, upload_id
            ):
                return cached_result
            if authoritative := await _authoritative_upload_result(db, user_id, upload_id):
                return authoritative

        real_mime = guess_mime_from_bytes(head)

        if real_mime != "application/octet-stream":
            safe_name, ext = _apply_mime_correction(
                safe_name, real_mime, ext, allowed_mimes=allowed_mimes
            )

        mime_type = real_mime
        if mime_type == "application/octet-stream":
            client_mime = (file.content_type or "").strip()
            mime_type = MimeRegistry.resolve_upload_mime(safe_name, client_mime or real_mime)

        _check_per_type_size(mime_type, pf.size)

        # SVG safety check
        if mime_type == "image/svg+xml":
            try:
                with pf.open("rb") as fh:
                    check_svg_safety_stream(fh, safe_name)
            except SvgSecurityError as exc:
                raise BadRequestError(str(exc), code=UploadErrorCode.SVG_UNSAFE) from exc

        file_sha256 = await pf.sha256()

        if not idem_cache_key:
            # Content-aware idempotency check (runs regardless of X-Upload-ID)
            idem_cache_key = f"{_IDEM_KEY_PREFIX}{user_id}:{upload_id}:{file_sha256}"
            if cached_result := await _validated_idempotency_cache_hit(
                redis, db, idem_cache_key, user_id, upload_id
            ):
                return cached_result

        if idem_header:
            # This is the ownership boundary. No capacity/quota reservation or
            # object-store mutation may happen before the transaction lock and
            # authoritative PostgreSQL re-check complete.
            if authoritative := await _claim_direct_upload_idempotency(db, user_id, upload_id):
                return authoritative

        await _reserve_storage_limit(pf.size, upload_id, redis, db)
        storage_reserved = True

        quarantine_key = f"quarantine/{user_id}/{upload_id}/{safe_name}"

        # Pending upload cap
        await _check_pending_cap(
            user_id,
            redis,
            db,
            privileged=user.role in PRIVILEGED_ROLES,
            reserve_key=quarantine_key,
        )
        quota_reserved = True

        # Stream file to quarantine
        async with get_s3_client() as s3:
            await s3.upload_file(  # type: ignore[call-arg]
                Filename=str(pf.path),
                Bucket=settings.s3_bucket,
                Key=quarantine_key,
                ExtraArgs={"ContentType": mime_type},
            )
        object_uploaded = True

        await _create_upload_row(
            upload_id=upload_id,
            user_id=user_id,
            quarantine_key=quarantine_key,
            filename=safe_name,
            mime_type=mime_type,
            size_bytes=pf.size,
            db=db,
        )

        _queue_processing_after_commit(
            db,
            user_id,
            upload_id,
            quarantine_key,
            safe_name,
            mime_type,
            file_size=pf.size,
        )

        result = UploadPendingOut(
            upload_id=upload_id,
            file_key=quarantine_key,
            status=UploadStatus.PENDING,
            size=pf.size,
            mime_type=mime_type,
        )

        if idem_cache_key and not _cache_idempotency_after_commit(
            db, redis, idem_cache_key, result
        ):
            logger.warning(
                "Upload idempotency cache was not registered because the DB session "
                "is not request-transaction managed"
            )

        return result

    except BaseException:
        if object_uploaded and quarantine_key is not None:
            with contextlib.suppress(Exception):
                await delete_object(quarantine_key)
        if quota_reserved and quarantine_key is not None:
            with contextlib.suppress(Exception):
                await redis.zrem(f"quota:uploads:{user_id}", quarantine_key)
        if storage_reserved:
            with contextlib.suppress(Exception):
                await _release_storage_reservation(upload_id, redis)
        raise
    finally:
        pf.cleanup()
