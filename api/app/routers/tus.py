"""tus 1.0.0 resumable upload server.

Protocol: https://tus.io/protocols/resumable-upload.html
Extensions supported: creation, termination, checksum

Upload lifecycle
----------------
1. POST   /api/upload/tus          – create upload, get Location URL
2. HEAD   /api/upload/tus/{tus_id} – query current offset
3. PATCH  /api/upload/tus/{tus_id} – append chunk; final PATCH returns X-WikINT-File-Key
4. DELETE /api/upload/tus/{tus_id} – terminate (abort S3 multipart, free Redis state)

S3 backend
----------
Each tus upload maps to one S3 multipart upload.  Parts are uploaded directly
during PATCH; on the final PATCH the multipart upload is completed and the
process_upload ARQ job is enqueued (same worker as the presigned path).
"""

import asyncio
import base64
import contextlib
import hashlib as _hashlib
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import MAGIC_HEADER_SIZE, PRIVILEGED_ROLES
from app.core.database import get_db
from app.core.exceptions import AppError, BadRequestError, ForbiddenError, NotFoundError
from app.core.mimetypes import MimeRegistry, guess_mime_from_bytes
from app.core.redis import get_redis, redis_lock
from app.core.storage import (
    abort_multipart_upload,
    complete_multipart_upload,
    create_multipart_upload,
    upload_part,
)
from app.core.upload_errors import (
    ERR_FILE_TOO_LARGE,
    ERR_TUS_CHECKSUM_MISMATCH,
    ERR_TUS_CONCURRENCY_LIMIT,
    ERR_TUS_CONTENT_TYPE,
    ERR_TUS_INVALID_OFFSET,
    ERR_TUS_UPLOAD_NOT_FOUND,
    ERR_TYPE_NOT_ALLOWED,
)
from app.dependencies.auth import CurrentUser
from app.dependencies.rate_limit import rate_limit_uploads
from app.routers.upload.helpers import (
    _check_pending_cap,
    _create_upload_row,
    _enqueue_processing,
)
from app.routers.upload.validators import _check_per_type_size, _validate_filename

logger = logging.getLogger("wikint")

router = APIRouter(prefix="/api/upload/tus", tags=["upload"])

TUS_VERSION = "1.0.0"
_TUS_STATE_PREFIX = "tus:state:"
_TUS_STATE_TTL = 24 * 3600
_TUS_ACTIVE_SESSIONS = "tus:active_sessions"

_S3_MIN_PART_BYTES = 5 * 1024 * 1024

_TUS_HEADERS = {
    "Tus-Resumable": TUS_VERSION,
}


def _tus_headers(**extra: str) -> dict[str, str]:
    return {**_TUS_HEADERS, **extra}


def _decode_upload_metadata(header: str | None) -> dict[str, str]:
    """Parse the Upload-Metadata header (comma-separated key base64value pairs)."""
    result: dict[str, str] = {}
    if not header:
        return result
    for item in header.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(" ", 1)
        key = parts[0].strip()
        if len(parts) == 2:
            with contextlib.suppress(Exception):
                result[key] = base64.b64decode(parts[1].strip()).decode("utf-8", errors="replace")
        else:
            result[key] = ""
    return result


@router.options("", include_in_schema=False)
@router.options("/", include_in_schema=False)
async def tus_options(
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> Response:
    max_size = settings.max_file_size_mb * 1024 * 1024

    return Response(
        status_code=204,
        headers=_tus_headers(
            **{
                "Tus-Version": TUS_VERSION,
                "Tus-Max-Size": str(max_size),
                "Tus-Extension": "creation,termination,checksum",
                "Tus-Checksum-Algorithm": "sha256",
            }
        ),
    )


@router.post("", status_code=201)
@router.post("/", status_code=201, include_in_schema=False)
async def tus_create(
    request: Request,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
    _rl: Annotated[None, Depends(rate_limit_uploads)],
) -> Response:
    """Create a new resumable upload.  Returns Location pointing to the upload resource."""
    user_id = str(user.id)

    if request.headers.get("Tus-Resumable") != TUS_VERSION:
        raise BadRequestError("Tus-Resumable header must be 1.0.0")

    raw_length = request.headers.get("Upload-Length", "")
    try:
        upload_length = int(raw_length)
    except ValueError:
        raise BadRequestError("Upload-Length header must be an integer", code=ERR_FILE_TOO_LARGE)

    if upload_length <= 0:
        raise BadRequestError("Upload-Length must be > 0", code=ERR_FILE_TOO_LARGE)

    # Fetch dynamic config
    from app.services.auth import get_full_auth_config

    config = await get_full_auth_config(db, redis)

    max_bytes = (
        (  # type: ignore[operator]
            config.get("max_file_size_mb")
            if config.get("max_file_size_mb") is not None
            else settings.max_file_size_mb
        )
        * 1024
        * 1024
    )
    if upload_length > max_bytes:
        raise BadRequestError(
            f"Upload-Length {upload_length // (1024 * 1024)} MiB exceeds server maximum of {max_bytes // (1024 * 1024)} MiB.",
            code=ERR_FILE_TOO_LARGE,
        )

    metadata = _decode_upload_metadata(request.headers.get("Upload-Metadata", ""))
    raw_filename = metadata.get("filename") or metadata.get("name") or "unnamed"
    raw_mime = (
        metadata.get("filetype") or metadata.get("content_type") or "application/octet-stream"
    )

    # Process allowed lists
    allowed_exts: set[str] | None = None
    if config.get("allowed_extensions"):
        allowed_exts = {
            e.strip().lower() for e in config["allowed_extensions"].split(",") if e.strip()
        }
        if not all(e.startswith(".") for e in allowed_exts):
            allowed_exts = {e if e.startswith(".") else f".{e}" for e in allowed_exts}

    allowed_mimes: set[str] | None = None
    if config.get("allowed_mime_types"):
        allowed_mimes = {
            m.strip().lower() for m in config["allowed_mime_types"].split(",") if m.strip()
        }

    safe_name, _ext = _validate_filename(raw_filename, allowed_extensions=allowed_exts)

    # Resolve octet-stream to a proper MIME when the browser doesn't know the type.
    # The actual MIME is always re-detected from magic bytes during processing.
    resolved_mime = MimeRegistry.resolve_upload_mime(raw_filename, raw_mime)

    if not MimeRegistry.is_allowed_mime(resolved_mime, allowed=allowed_mimes):
        raise BadRequestError(
            f"MIME type '{resolved_mime}' is not allowed for upload.",
            code=ERR_TYPE_NOT_ALLOWED,
        )

    from app.routers.upload.helpers import _check_storage_limit

    await _check_storage_limit(upload_length, config=config)

    _check_per_type_size(raw_mime, upload_length, config=config)

    tus_id = str(uuid.uuid4())
    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user_id}/{upload_id}/{safe_name}"
    await _check_pending_cap(
        user_id,
        redis,
        privileged=user.role in PRIVILEGED_ROLES,
        reserve_key=quarantine_key,
    )

    s3_upload_id = await create_multipart_upload(
        quarantine_key,
        content_type=resolved_mime,
        content_disposition="",
    )

    state: dict[str, str] = {
        "user_id": user_id,
        "upload_id": upload_id,
        "quarantine_key": quarantine_key,
        "s3_upload_id": s3_upload_id,
        "filename": safe_name,
        "mime_type": resolved_mime,
        "offset": "0",
        "length": str(upload_length),
        "parts": "[]",
    }
    await redis.hset(f"{_TUS_STATE_PREFIX}{tus_id}", mapping=state)  # type: ignore[arg-type, misc]
    await redis.expire(f"{_TUS_STATE_PREFIX}{tus_id}", _TUS_STATE_TTL)
    await redis.sadd(_TUS_ACTIVE_SESSIONS, tus_id)  # type: ignore[misc]

    await _create_upload_row(
        upload_id=upload_id,
        user_id=user_id,
        quarantine_key=quarantine_key,
        filename=safe_name,
        mime_type=resolved_mime,
        size_bytes=upload_length,
    )

    location = f"{request.base_url}api/upload/tus/{tus_id}"
    return Response(
        status_code=201,
        headers=_tus_headers(**{"Location": location}),
    )


@router.head("/{tus_id}", include_in_schema=False)
async def tus_head(
    tus_id: uuid.UUID,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> Response:
    """Return current Upload-Offset for a pending upload."""
    state = await _load_state(str(tus_id), user, redis)
    return Response(
        status_code=200,
        headers=_tus_headers(
            **{
                "Upload-Offset": state["offset"],
                "Upload-Length": state["length"],
                "Cache-Control": "no-store",
            }
        ),
    )


@router.patch("/{tus_id}")
async def tus_patch(
    tus_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Append a chunk to the upload at Upload-Offset.

    On the final chunk, completes the S3 multipart upload and enqueues
    background processing.  Returns X-WikINT-File-Key for the SSE stream.
    """
    tus_id_str = str(tus_id)
    _inflight_key = f"tus:inflight:{user.id}"
    inflight = await redis.incr(_inflight_key)
    # TTL ensures the counter self-heals if the process crashes before decr.
    await redis.expire(_inflight_key, 300)

    # Fetch dynamic config
    from app.services.auth import get_full_auth_config

    config = await get_full_auth_config(db, redis)

    max_concurrent = (
        config.get("tus_max_concurrent_per_user") or settings.tus_max_concurrent_per_user
    )
    if inflight > max_concurrent:
        await redis.decr(_inflight_key)
        return Response(
            status_code=429,
            headers=_tus_headers(**{"X-WikINT-Error": ERR_TUS_CONCURRENCY_LIMIT}),
        )

    try:
        if request.headers.get("Content-Type", "") != "application/offset+octet-stream":
            raise BadRequestError(
                "Content-Type must be application/offset+octet-stream",
                code=ERR_TUS_CONTENT_TYPE,
            )

        raw_offset = request.headers.get("Upload-Offset", "")
        try:
            client_offset = int(raw_offset)
        except ValueError:
            raise BadRequestError(
                "Upload-Offset header must be an integer", code=ERR_TUS_INVALID_OFFSET
            )

        try:
            chunk_size = int(request.headers.get("Content-Length", "0"))
        except ValueError:
            raise BadRequestError("Content-Length must be an integer.")

        chunk_max = config.get("tus_chunk_max_bytes") or settings.tus_chunk_max_bytes
        if chunk_size > chunk_max:
            raise BadRequestError(f"Chunk too large: maximum {chunk_max} bytes, got {chunk_size}")

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()

        try:
            hasher = _hashlib.sha256()
            if chunk_size > 0:
                total_read = 0

                def _write_and_hash(data: bytes) -> None:
                    with open(tmp_path, "ab") as f:
                        f.write(data)
                    hasher.update(data)

                async for data_chunk in request.stream():
                    total_read += len(data_chunk)
                    if total_read > chunk_size or total_read > chunk_max:
                        raise BadRequestError(
                            "Payload size exceeded Content-Length or maximum limits."
                        )
                    await asyncio.to_thread(_write_and_hash, data_chunk)

                if total_read != chunk_size:
                    raise BadRequestError(
                        f"Content-Length mismatch: expected {chunk_size}, got {total_read}"
                    )

                checksum_header = request.headers.get("Upload-Checksum", "")
                if checksum_header:
                    cs_parts = checksum_header.split(" ")
                    if len(cs_parts) == 2 and cs_parts[0] == "sha256":
                        provided_b64 = cs_parts[1]
                        calculated_b64 = base64.b64encode(hasher.digest()).decode()
                        if provided_b64 != calculated_b64:
                            raise AppError(
                                status_code=460,
                                detail="Upload-Checksum mismatch.",
                                code=ERR_TUS_CHECKSUM_MISMATCH,
                            )

            async with redis_lock(redis, f"tus:{tus_id_str}", timeout=120.0):
                state = await _load_state(tus_id_str, user, redis)
                current_offset = int(state["offset"])
                total_length = int(state["length"])

                if state.get("sniffed") != "1" and current_offset < MAGIC_HEADER_SIZE:
                    if (current_offset == 0 and chunk_size > 0) or (
                        current_offset < MAGIC_HEADER_SIZE and chunk_size > 0
                    ):
                        is_final = (current_offset + chunk_size) == total_length

                        if current_offset == 0 and (chunk_size >= MAGIC_HEADER_SIZE or is_final):
                            with open(tmp_path, "rb") as f:
                                head = f.read(MAGIC_HEADER_SIZE)
                            detected_mime = guess_mime_from_bytes(head)

                            # Allow minor variations (e.g. text/x-tex vs application/x-tex) if both
                            # are valid for the file's extension.
                            ext = Path(state["filename"]).suffix.lower()
                            allowed_for_ext = MimeRegistry.get_allowed_mimes_for_extension(ext)
                            is_compatible = (
                                (
                                    detected_mime in allowed_for_ext
                                    and state["mime_type"] in allowed_for_ext
                                )
                                if allowed_for_ext
                                else False
                            )

                            if (
                                detected_mime != "application/octet-stream"
                                and detected_mime != state["mime_type"]
                                and not is_compatible
                            ):
                                logger.warning(
                                    "TUS MIME mismatch for %s: declared %s, detected %s",
                                    tus_id_str,
                                    state["mime_type"],
                                    detected_mime,
                                )
                                raise BadRequestError(
                                    f"File content ({detected_mime}) does not match declared type ({state['mime_type']}).",
                                    code=ERR_TUS_CONTENT_TYPE,
                                )
                            await redis.hset(f"{_TUS_STATE_PREFIX}{tus_id_str}", "sniffed", "1")  # type: ignore[misc]
                        elif (
                            current_offset == 0 and chunk_size < MAGIC_HEADER_SIZE and not is_final
                        ):
                            pass

                if client_offset != current_offset:
                    raise AppError(
                        status_code=409,
                        detail=f"Upload-Offset mismatch: expected {current_offset}, got {client_offset}.",
                        code=ERR_TUS_INVALID_OFFSET,
                    )

                if chunk_size == 0:
                    return Response(
                        status_code=204,
                        headers=_tus_headers(**{"Upload-Offset": str(current_offset)}),
                    )

                if current_offset + chunk_size > total_length:
                    raise BadRequestError(
                        f"Chunk overflows declared Upload-Length by {(current_offset + chunk_size) - total_length} bytes.",
                        code=ERR_FILE_TOO_LARGE,
                    )

                is_final = (current_offset + chunk_size) == total_length

                chunk_min = config.get("tus_chunk_min_bytes") or settings.tus_chunk_min_bytes
                if not is_final and chunk_size < chunk_min:
                    raise BadRequestError(
                        f"Non-final chunk too small: minimum {chunk_min} bytes, got {chunk_size}"
                    )
                parts: list[dict[str, int | str]] = json.loads(state["parts"])
                part_number = len(parts) + 1

                # Read chunk into memory for upload_part (Fix 500 error)
                chunk_bytes = await asyncio.to_thread(tmp_path.read_bytes)

                etag = await upload_part(
                    state["quarantine_key"],
                    state["s3_upload_id"],
                    part_number,
                    chunk_bytes,
                )

                parts.append({"PartNumber": part_number, "ETag": etag})

                new_offset = current_offset + chunk_size

                state_key = f"{_TUS_STATE_PREFIX}{tus_id_str}"
                await redis.hset(  # type: ignore[misc]
                    state_key,
                    mapping={"offset": str(new_offset), "parts": json.dumps(parts)},
                )
                await redis.expire(state_key, _TUS_STATE_TTL)  # type: ignore[misc]

                if is_final:
                    quarantine_key = state["quarantine_key"]
                    try:
                        await complete_multipart_upload(
                            quarantine_key,
                            state["s3_upload_id"],
                            parts,
                        )
                    except Exception as exc:
                        if "NoSuchUpload" in str(exc):
                            from app.core.storage import object_exists

                            if await object_exists(quarantine_key):
                                logger.info(
                                    "Upload %s already completed in S3, proceeding.", tus_id_str
                                )
                            else:
                                logger.error(
                                    "S3 complete_multipart_upload failed for tus %s: %s",
                                    tus_id_str,
                                    exc,
                                )
                                await redis.delete(state_key)
                                raise BadRequestError(
                                    "Failed to complete upload (not found). Please restart."
                                )
                        else:
                            logger.error(
                                "S3 complete_multipart_upload failed for tus %s: %s",
                                tus_id_str,
                                exc,
                            )
                            await abort_multipart_upload(quarantine_key, state["s3_upload_id"])
                            await redis.delete(state_key)
                            raise BadRequestError("Failed to complete upload. Please retry.")

                    import time

                    await redis.zadd(
                        f"quota:uploads:{state['user_id']}", {quarantine_key: time.time()}
                    )

                    await _enqueue_processing(
                        user_id=state["user_id"],
                        upload_id=state["upload_id"],
                        quarantine_key=quarantine_key,
                        filename=state["filename"],
                        mime_type=state["mime_type"],
                        file_size=total_length,
                    )

                    await redis.delete(state_key)
                    await redis.srem(_TUS_ACTIVE_SESSIONS, tus_id_str)  # type: ignore[misc]

                    return Response(
                        status_code=204,
                        headers=_tus_headers(
                            **{
                                "Upload-Offset": str(new_offset),
                                "X-WikINT-File-Key": quarantine_key,
                            }
                        ),
                    )

                return Response(
                    status_code=204,
                    headers=_tus_headers(**{"Upload-Offset": str(new_offset)}),
                )
        finally:
            tmp_path.unlink(missing_ok=True)
    finally:
        await redis.decr(_inflight_key)


@router.delete("/{tus_id}", status_code=204)
async def tus_delete(
    tus_id: uuid.UUID,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> Response:
    """Terminate a pending upload.  Aborts the S3 multipart upload."""
    state = await _load_state(str(tus_id), user, redis)

    await abort_multipart_upload(state["quarantine_key"], state["s3_upload_id"])

    await redis.delete(f"{_TUS_STATE_PREFIX}{str(tus_id)}")
    await redis.srem(_TUS_ACTIVE_SESSIONS, str(tus_id))  # type: ignore[misc]

    return Response(status_code=204, headers=_TUS_HEADERS)


async def _load_state(tus_id: str, user: CurrentUser, redis: Redis) -> dict[str, str]:  # type: ignore[type-arg]
    """Load tus state from Redis, enforcing ownership."""
    state_key = f"{_TUS_STATE_PREFIX}{tus_id}"
    state: dict = await redis.hgetall(state_key)  # type: ignore[type-arg, misc]
    if not state:
        raise NotFoundError("Upload not found or expired.", code=ERR_TUS_UPLOAD_NOT_FOUND)

    decoded: dict[str, str] = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in state.items()
    }

    if decoded.get("user_id") != str(user.id):
        raise ForbiddenError("Upload does not belong to you")

    return decoded
