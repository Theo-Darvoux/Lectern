from __future__ import annotations

import difflib
import gzip
import mimetypes as _mimetypes
import uuid
import zlib
from io import BytesIO
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.exceptions import BadRequestError, NotFoundError
from app.core.common.upload_errors import UploadErrorCode
from app.core.common.upload_keys import UPLOAD_QUOTA_PREFIX
from app.core.common.upload_limits import enforce_upload_size_limit
from app.core.database.database import get_db
from app.core.database.post_commit import register_transaction_callbacks
from app.core.database.redis import RedisClient, get_redis, redis_client
from app.core.storage.facade import (
    generate_presigned_get,
    generate_presigned_get_cached,
    read_full_object,
    stream_object,
)
from app.core.storage.facade import (
    upload_file as storage_upload_file,
)
from app.dependencies.auth import CurrentUser, ReadUser, require_moderator
from app.dependencies.rate_limit import (
    rate_limit_downloads,
    rate_limit_uploads,
    rate_limit_views,
)
from app.models.upload import Upload
from app.models.user import MODERATOR_ROLES, User, UserRole
from app.routers.upload.helpers import (
    _check_pending_cap,
    _processing_queue_name,
    _release_storage_reservation,
    _reserve_storage_limit,
)
from app.schemas.material import (
    MaterialDetailResponse,
    MaterialVersionResponse,
    project_material_detail,
    project_material_version,
)
from app.services.audit import record_download
from app.services.material import (
    get_material_attachments,
    get_material_file_info,
    get_material_thumbnail_info,
    get_material_version,
    get_material_versions,
    get_material_with_version,
    increment_download_count,
    record_view,
    resolve_thumbnail,
    set_favourite,
    toggle_like,
)

# Text MIME types that can be fetched / edited as plain text
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/typescript",
        "application/x-yaml",
        "application/x-sh",
        "application/sql",
    }
)
# MIME types that can be read as UTF-8 text but must NOT be edited via the text endpoint
# (they have their own security-checked upload paths).
_TEXT_READABLE_EXACT = frozenset({"image/svg+xml"})
_TEXT_EDIT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB cap on raw text body
_TEXT_DECOMPRESS_MAX_BYTES = _TEXT_EDIT_MAX_BYTES
# Viewers read at most this much text; larger files are cut and flagged with
# the ``X-Content-Truncated`` header so the client can offer the original.
_TEXT_VIEW_MAX_BYTES = 10 * 1024 * 1024
_TEXT_READ_CHUNK_BYTES = 64 * 1024
_TEXT_DIFF_MAX_BYTES = 1024 * 1024  # 1 MiB bound on text diff output
_TEXT_DIFF_INPUT_MAX_BYTES = 1024 * 1024  # Detailed diffs only for <= 1 MiB inputs
_TEXT_DIFF_MAX_LINES = 10_000  # Bound split-list and SequenceMatcher state
_TEXT_DIFF_OMITTED = "... diff omitted: input too large ..."
_TEXT_STAGING_RESERVATION_TTL = max(
    48 * 3600,
    (settings.pr_expiry_days + 1) * 24 * 3600,
)
_GZIP_MAGIC = b"\x1f\x8b"

router = APIRouter(prefix="/api/materials", tags=["materials"])


async def _presigned_url(
    key: str,
    redis: Redis | None,
    *,
    force_download: bool = True,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """Return a presigned URL, using the Redis-cached variant when Redis is available."""
    if redis is not None:
        return await generate_presigned_get_cached(
            key,
            redis=redis,
            force_download=force_download,
            filename=filename,
            content_type=content_type,
        )
    return await generate_presigned_get(
        key,
        force_download=force_download,
        filename=filename,
        content_type=content_type,
    )


async def _current_file(db: AsyncSession, material_id: str, missing: str) -> Any:
    """The material's current version, loaded with one JOIN query.

    File endpoints only need the version row; the full material detail (counts,
    likes, thumbnail signing) is not worth its half-dozen queries here.
    """
    try:
        version = await get_material_file_info(db, material_id)
    except NotFoundError:
        raise NotFoundError(missing) from None
    if not version.file_key:
        raise NotFoundError(missing)
    return version


def _is_text_mime(mime: str) -> bool:
    """Return True if this MIME type can be represented as editable UTF-8 text."""
    m = (mime or "").lower()
    if any(m.startswith(p) for p in _TEXT_MIME_PREFIXES):
        return True
    return m in _TEXT_MIME_EXACT


def _decompress_gzip_text(
    raw_bytes: bytes,
    *,
    max_output_bytes: int = _TEXT_DECOMPRESS_MAX_BYTES,
) -> bytes:
    """Decompress legacy gzip-wrapped text with a strict output bound."""
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes must be non-negative")

    chunks: list[bytes] = []
    total = 0
    try:
        with gzip.GzipFile(fileobj=BytesIO(raw_bytes), mode="rb") as stream:
            while True:
                remaining = max_output_bytes - total
                chunk = stream.read(min(64 * 1024, remaining + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_output_bytes:
                    raise BadRequestError(
                        "Decompressed text exceeds the maximum editable-text size"
                    )
                chunks.append(chunk)
    except BadRequestError:
        raise
    except (EOFError, OSError, zlib.error) as exc:
        raise BadRequestError("Failed to decompress gzip-wrapped text") from exc

    return b"".join(chunks)


_SPLITLINE_SEPARATORS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


def _exceeds_splitline_limit(text: str, max_lines: int) -> bool:
    """Return True before splitlines() would materialize more than max_lines entries."""
    if max_lines < 1:
        return bool(text)

    lines = 1
    previous_was_cr = False
    for char in text:
        if char == "\n" and previous_was_cr:
            previous_was_cr = False
            continue
        previous_was_cr = char == "\r"
        if char not in _SPLITLINE_SEPARATORS:
            previous_was_cr = False
            continue
        lines += 1
        if lines > max_lines:
            return True
    return False


def _build_bounded_text_diff(
    old_text: str,
    new_text: str,
    *,
    old_size_bytes: int,
    new_size_bytes: int,
    fromfile: str,
    tofile: str,
) -> str:
    """Build a bounded unified diff without materializing hostile line lists first."""
    if (
        old_size_bytes > _TEXT_DIFF_INPUT_MAX_BYTES
        or new_size_bytes > _TEXT_DIFF_INPUT_MAX_BYTES
        or _exceeds_splitline_limit(old_text, _TEXT_DIFF_MAX_LINES)
        or _exceeds_splitline_limit(new_text, _TEXT_DIFF_MAX_LINES)
    ):
        return f"```diff\n{_TEXT_DIFF_OMITTED}\n```"

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines: list[str] = []
    diff_size = 0
    for line in difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=fromfile,
        tofile=tofile,
        lineterm="",
    ):
        encoded_line_len = len(line.encode("utf-8")) + 1
        if diff_size + encoded_line_len > _TEXT_DIFF_MAX_BYTES:
            diff_lines.append("... diff truncated ...")
            break
        diff_lines.append(line)
        diff_size += encoded_line_len

    return "```diff\n" + "\n".join(diff_lines) + "\n```" if diff_lines else ""


@router.get("/{material_id}", response_model=MaterialDetailResponse)
async def get_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MaterialDetailResponse:
    data = await get_material_with_version(db, material_id, current_user_id=user.id)
    return project_material_detail(data, public=user.role == UserRole.GUEST)


@router.get("/{material_id}/download-url")
async def get_material_download_url(
    material_id: str,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_downloads)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,
) -> dict[str, Any]:
    version = await _current_file(db, material_id, "No file available for download")

    file_mime = version.file_mime_type or ""
    file_name = version.file_name or ""
    is_pdf = file_mime == "application/pdf" or file_name.lower().endswith(".pdf")

    url = await _presigned_url(
        version.file_key,
        redis,
        force_download=not is_pdf,
        filename=version.file_name,
        content_type=version.file_mime_type,
    )

    await increment_download_count(db, material_id)
    await record_download(
        db,
        user.id,
        uuid.UUID(material_id),
        version.version_number,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    return {"url": url, "filename": version.file_name}


@router.get("/{material_id}/inline")
async def inline_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,
) -> dict[str, Any]:
    version = await _current_file(db, material_id, "No file available for preview")

    # Images, PDFs, and Videos are safe to render inline; all other types are forced
    # to download so the browser never executes or parses unknown content.
    file_mime = version.file_mime_type or ""
    inline_safe = (
        file_mime.startswith("image/")
        or file_mime.startswith("video/")
        or file_mime == "application/pdf"
    )
    url = await _presigned_url(
        version.file_key,
        redis,
        force_download=not inline_safe,
        filename=version.file_name,
        content_type=version.file_mime_type,
    )
    return {"url": url, "filename": version.file_name}


@router.get("/{material_id}/thumbnail")
async def thumbnail_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,
) -> dict[str, Any]:
    """Return a signed URL for a material's card preview.

    Returns {"url": ..., "thumbnail_type": "webp" | "fallback"}.
    - "webp": a generated WebP thumbnail.
    - "fallback": no generated thumbnail; the original image, video or PDF, which
      the browser renders itself.
    Raises 404 for types without either (text, Office, audio, archives…).
    """
    try:
        mid = uuid.UUID(material_id)
    except ValueError:
        raise NotFoundError("Material not found") from None
    version = await get_material_thumbnail_info(db, mid, redis)
    if not version:
        raise NotFoundError("Material version not found")
    resolved = await resolve_thumbnail(version, redis)
    if resolved is None:
        raise NotFoundError("Thumbnail not available for this file type")
    url, thumbnail_type = resolved
    return {"url": url, "thumbnail_type": thumbnail_type}


@router.post("/{material_id}/recalculate-thumbnail")
async def recalculate_material_thumbnail(
    material_id: str,
    user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Force recreate the thumbnail for a material.

    Moderator or admin privileges required. Dispatches the recalculation job and
    waits briefly for it. ``status`` is the job result ("ok", "skipped" or
    "failed"), or "queued" when the job is still running. The worker invalidates
    the thumbnail caches itself.
    """
    import time
    from typing import cast

    import app.core.database.redis as redis_core

    version = await _current_file(db, material_id, "No file available for thumbnail generation")
    target_version_id = version.id

    if redis_core.arq_pool is None:
        raise BadRequestError("Background processing is temporarily unavailable. Please try again.")

    queue_name = _processing_queue_name(version.file_mime_type or "", version.file_size or 0)
    enqueue_job = cast(Any, redis_core.arq_pool.enqueue_job)
    job = await enqueue_job(
        "recalculate_thumbnail",
        _queue_name=queue_name,
        material_id=str(material_id),
        version_id=str(target_version_id) if target_version_id else None,
    )

    status = "queued"
    if job is not None:
        try:
            res = await job.result(timeout=15.0, poll_delay=0.2)
            if res in ("ok", "skipped", "failed"):
                status = res
        except Exception:
            status = "queued"

    return {
        "status": status,
        "material_id": str(material_id),
        "version_id": str(target_version_id) if target_version_id else None,
        "timestamp": int(time.time() * 1000),
    }


@router.get("/{material_id}/file")
async def stream_material_file(
    material_id: str,
    user: ReadUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)] = None,
) -> Any:
    """Redirect a read-authenticated client to an immutable presigned object URL.

    This is the in-page media stream (video/audio players). Playback is not a
    download: it is neither recorded nor charged to the download quota. Explicit
    downloads go through ``/download-url``.
    """
    if redis is None:
        redis = redis_client

    version = await _current_file(db, material_id, "No file available")

    # S3/MinIO handles Range requests (206), which native media players require.
    file_mime = version.file_mime_type or ""
    inline_safe = (
        file_mime.startswith("image/")
        or file_mime.startswith("video/")
        or file_mime.startswith("audio/")
        or file_mime == "application/pdf"
    )
    url = await _presigned_url(
        version.file_key,
        redis,
        force_download=not inline_safe,
        filename=version.file_name,
        content_type=file_mime or None,
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/{material_id}/versions", response_model=list[MaterialVersionResponse])
async def list_versions(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MaterialVersionResponse]:
    versions = await get_material_versions(db, material_id)
    public = user.role == UserRole.GUEST
    return [project_material_version(version, public=public) for version in versions]


@router.get(
    "/{material_id}/versions/{version_number}",
    response_model=MaterialVersionResponse,
)
async def get_version(
    material_id: str,
    version_number: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MaterialVersionResponse:
    version = await get_material_version(db, material_id, version_number)
    return project_material_version(version, public=user.role == UserRole.GUEST)


@router.get("/{material_id}/versions/{version_number}/download-url")
async def get_version_download_url(
    material_id: str,
    version_number: int,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_downloads)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,
) -> dict[str, Any]:
    version = await get_material_version(db, material_id, version_number)
    if not version.file_key:
        raise NotFoundError("No file available for download")

    file_mime = version.file_mime_type or ""
    file_name = version.file_name or ""
    is_pdf = file_mime == "application/pdf" or file_name.lower().endswith(".pdf")

    url = await _presigned_url(
        version.file_key,
        redis,
        force_download=not is_pdf,
        filename=version.file_name,
        content_type=version.file_mime_type,
    )

    await record_download(
        db,
        user.id,
        uuid.UUID(material_id),
        version_number,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    return {"url": url, "filename": version.file_name}


@router.get("/{material_id}/attachments", response_model=list[MaterialDetailResponse])
async def list_attachments(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MaterialDetailResponse]:
    attachments = await get_material_attachments(db, material_id, current_user_id=user.id)
    public = user.role == UserRole.GUEST
    return [project_material_detail(a, public=public) for a in attachments]


@router.post("/{material_id}/view")
async def view_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_views)],
) -> dict[str, str]:
    await record_view(db, str(user.id), material_id)
    return {"status": "ok"}


@router.post("/{material_id}/like")
async def like_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    try:
        uid = uuid.UUID(material_id)
    except ValueError:
        raise BadRequestError(f"Invalid material ID: {material_id}")
    liked = await toggle_like(db, user.id, uid)
    await db.commit()
    return {"liked": liked}


@router.put("/{material_id}/favourite")
async def favourite_material(
    material_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await set_favourite(db, user.id, material_id, favourited=True)
    await db.commit()
    return {"favourited": True}


@router.delete("/{material_id}/favourite")
async def unfavourite_material(
    material_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await set_favourite(db, user.id, material_id, favourited=False)
    await db.commit()
    return {"favourited": False}


# ---------------------------------------------------------------------------
# Text-content endpoints (for inline text editing)
# ---------------------------------------------------------------------------


@router.get("/{material_id}/text-content", response_class=PlainTextResponse)
async def get_material_text_content(
    material_id: str,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Return the UTF-8 text of the material's current version.

    Works for both plain-text files and gzip-compressed text files (.gz).
    Only available for text-based MIME types. At most ``_TEXT_VIEW_MAX_BYTES``
    of (decompressed) text is returned; a longer file is cut and the response
    carries ``X-Content-Truncated: 1``. The ETag is the version's file key, so
    reopening an unchanged file is a 304.
    """
    version = await _current_file(db, material_id, "No file available")

    mime = (version.file_mime_type or "").lower()
    filename = (version.file_name or "").lower()

    # Allow gzip-wrapped text files (e.g. original.md.gz)
    is_gzip_wrapped = mime == "application/gzip" or filename.endswith(".gz")

    if not is_gzip_wrapped and not _is_text_mime(mime) and mime not in _TEXT_READABLE_EXACT:
        raise BadRequestError("This file is not a text-based document and cannot be edited as text")

    etag = f'"{version.file_key}:{_TEXT_VIEW_MAX_BYTES}"'
    cache_headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)

    raw_bytes, truncated = await _read_text_prefix(
        version.file_key, gzip_hint=is_gzip_wrapped, limit=_TEXT_VIEW_MAX_BYTES
    )

    # Detect and strip UTF-8 BOM if present
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]

    text = _decode_text(raw_bytes, truncated=truncated)
    headers = dict(cache_headers)
    if truncated:
        headers["X-Content-Truncated"] = "1"
    return PlainTextResponse(content=text, media_type="text/plain; charset=utf-8", headers=headers)


async def _read_text_prefix(file_key: str, *, gzip_hint: bool, limit: int) -> tuple[bytes, bool]:
    """Read at most ``limit`` bytes of text, decompressing gzip on the fly.

    Returns the bytes and whether the file was longer. Only the needed prefix
    is pulled from storage, and gzip output is bounded per input chunk, so a
    huge file or a gzip bomb never lands in memory. Magic bytes are
    authoritative: a disguised gzip stream is decoded, never served raw.
    """
    out = bytearray()
    decoder: Any = None
    async with stream_object(file_key) as body:
        first = True
        while True:
            chunk: bytes = await body.read(_TEXT_READ_CHUNK_BYTES)
            if first:
                first = False
                if gzip_hint or chunk.startswith(_GZIP_MAGIC):
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            if not chunk:
                return bytes(out), False
            if decoder is None:
                out += chunk
                if len(out) > limit:
                    return bytes(out[:limit]), True
                continue
            data = chunk
            try:
                while data:
                    piece = decoder.decompress(data, limit + 1 - len(out))
                    out += piece
                    if len(out) > limit:
                        return bytes(out[:limit]), True
                    tail = decoder.unconsumed_tail
                    if not piece and tail == data:
                        break
                    data = tail
            except zlib.error as exc:
                raise BadRequestError("Failed to decompress gzip-wrapped text") from exc


def _decode_text(raw_bytes: bytes, *, truncated: bool) -> str:
    """Decode UTF-8, tolerating a multi-byte character split by truncation."""
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        if truncated and exc.start >= len(raw_bytes) - 3:
            try:
                return raw_bytes[: exc.start].decode("utf-8")
            except UnicodeDecodeError:
                pass
        return raw_bytes.decode("latin-1")


@router.post("/{material_id}/text-content")
async def save_material_text_content(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: Annotated[str, Body(media_type="text/plain", max_length=_TEXT_EDIT_MAX_BYTES)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,
    _: Annotated[None, Depends(rate_limit_uploads)] = None,
) -> dict[str, Any]:
    """Accept raw UTF-8 text, gzip-compress it server-side, store to object storage.

    Creates a clean Upload row so the returned file_key passes PR validation.
    Returns ``{ file_key, file_name, file_size, file_mime_type }`` ready
    to be included in an ``edit_material`` PR operation.
    """
    redis_inst = redis if redis is not None else redis_client
    user_id_str = str(user.id)
    user_role = user.role

    version = await _current_file(db, material_id, "No version found for this material")

    current_mime = (version.file_mime_type or "").lower()
    current_name = version.file_name or ""

    # Strip any previous .gz suffix to derive the "logical" original name
    logical_name = current_name[:-3] if current_name.endswith(".gz") else current_name

    is_gzip_wrapped = current_mime == "application/gzip" or current_name.endswith(".gz")

    if is_gzip_wrapped:
        guessed, _encoding = _mimetypes.guess_type(logical_name)
        check_mime = guessed or "text/plain"

    else:
        check_mime = current_mime

    if not _is_text_mime(check_mime) and not is_gzip_wrapped:
        raise BadRequestError("Cannot save text content for a non-text file")

    raw_bytes = body.encode("utf-8")
    file_size = len(raw_bytes)

    if file_size > _TEXT_EDIT_MAX_BYTES:
        raise BadRequestError(
            "Text content exceeds the 10 MiB editor limit",
            code=UploadErrorCode.FILE_TOO_LARGE,
        )

    enforce_upload_size_limit(check_mime, file_size)

    # Compute text diff. Detailed diffing has a smaller resource budget than
    # the editor itself; preflight byte/line bounds before splitlines()/difflib.
    old_bytes = b""
    try:
        old_bytes = await read_full_object(version.file_key)
        if is_gzip_wrapped or old_bytes.startswith(_GZIP_MAGIC):
            old_bytes = _decompress_gzip_text(old_bytes)
        if old_bytes.startswith(b"\xef\xbb\xbf"):
            old_bytes = old_bytes[3:]
        try:
            old_text = old_bytes.decode("utf-8")
        except UnicodeDecodeError:
            old_text = old_bytes.decode("latin-1")
    except Exception:
        old_bytes = b""
        old_text = ""

    diff_text = _build_bounded_text_diff(
        old_text,
        body,
        old_size_bytes=len(old_bytes),
        new_size_bytes=file_size,
        fromfile=current_name,
        tofile=logical_name,
    )

    # Build deterministic storage key scoped to the user
    upload_id = str(uuid.uuid4())
    file_key = f"uploads/{user_id_str}/{upload_id}/{logical_name}"
    staging_quota_key = f"staging:{user_id_str}:{upload_id}"

    async def _noop() -> None:
        return None

    async def rollback_object() -> None:
        from app.core.storage.facade import delete_object

        await delete_object(file_key)

    async def rollback_quota() -> None:
        await redis_inst.zrem(f"{UPLOAD_QUOTA_PREFIX}{user_id_str}", staging_quota_key)

    async def rollback_storage_reservation() -> None:
        await _release_storage_reservation(upload_id, redis_inst)

    for rollback in (rollback_storage_reservation, rollback_quota, rollback_object):
        if not register_transaction_callbacks(
            db,
            on_rollback=rollback,
            on_commit=_noop,
        ):
            raise RuntimeError("Text-content storage writes require a request-managed transaction")

    await _reserve_storage_limit(
        file_size,
        upload_id,
        redis_inst,
        db,
        ttl_seconds=_TEXT_STAGING_RESERVATION_TTL,
    )
    await _check_pending_cap(
        user_id_str,
        redis_inst,
        db,
        privileged=user_role in MODERATOR_ROLES,
        reserve_key=staging_quota_key,
    )

    # Upload to object storage. The registered compensation removes this unique
    # key if upload lost-success or the following database transaction fails.
    await storage_upload_file(
        raw_bytes,
        file_key,
        content_type=check_mime,
        content_encoding=None,
        content_disposition="attachment",
    )

    # Create a clean Upload row so PR key validation passes
    upload_row = Upload(
        upload_id=upload_id,
        user_id=user.id,
        quarantine_key=None,
        final_key=file_key,
        status="clean",
        filename=logical_name,
        mime_type=check_mime,
        size_bytes=file_size,
    )
    db.add(upload_row)

    return {
        "file_key": file_key,
        "file_name": logical_name,
        "file_size": file_size,
        "file_mime_type": check_mime,
        "diff": diff_text,
    }
