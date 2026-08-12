from __future__ import annotations

import difflib
import gzip
import mimetypes as _mimetypes
import uuid
import zlib
from io import BytesIO
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.constants import PRIVILEGED_ROLES
from app.core.common.exceptions import AppError, BadRequestError, NotFoundError
from app.core.common.upload_errors import UploadErrorCode
from app.core.common.upload_limits import enforce_upload_size_limit
from app.core.database.database import get_db
from app.core.database.post_commit import register_transaction_callbacks
from app.core.database.redis import get_redis, redis_client
from app.core.storage.facade import (
    generate_presigned_get,
    generate_presigned_get_cached,
    read_full_object,
)
from app.core.storage.facade import (
    upload_file as storage_upload_file,
)
from app.dependencies.auth import CurrentUser, ReadUser
from app.dependencies.rate_limit import (
    rate_limit_downloads,
    rate_limit_uploads,
    rate_limit_views,
)
from app.models.upload import Upload
from app.models.user import UserRole
from app.routers.upload.helpers import (
    _check_pending_cap,
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
    get_material_thumbnail_info,
    get_material_version,
    get_material_versions,
    get_material_with_version,
    increment_download_count,
    record_view,
    toggle_favourite,
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
    data = await get_material_with_version(db, material_id)
    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise NotFoundError("No file available for download")

    file_mime = version.get("file_mime_type") or ""
    file_name = version.get("file_name") or ""
    is_pdf = file_mime == "application/pdf" or file_name.lower().endswith(".pdf")

    url = await _presigned_url(
        version["file_key"],
        redis,
        force_download=not is_pdf,
        filename=version.get("file_name"),
        content_type=version.get("file_mime_type"),
    )

    await increment_download_count(db, material_id)
    await record_download(
        db,
        user.id,
        uuid.UUID(material_id),
        version["version_number"],
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    return {"url": url, "filename": version.get("file_name")}


@router.get("/{material_id}/inline")
async def inline_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,
) -> dict[str, Any]:
    data = await get_material_with_version(db, material_id)
    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise NotFoundError("No file available for preview")

    # Images, PDFs, and Videos are safe to render inline; all other types are forced
    # to download so the browser never executes or parses unknown content.
    file_mime = version.get("file_mime_type") or ""
    inline_safe = (
        file_mime.startswith("image/")
        or file_mime.startswith("video/")
        or file_mime == "application/pdf"
    )
    url = await _presigned_url(
        version["file_key"],
        redis,
        force_download=not inline_safe,
        filename=version.get("file_name"),
        content_type=version.get("file_mime_type"),
    )
    return {"url": url, "filename": version.get("file_name")}


@router.get("/{material_id}/thumbnail")
async def thumbnail_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,
) -> dict[str, Any]:
    """
    Generate a presigned URL for a material version's thumbnail.
    Returns {"url": ..., "thumbnail_type": "webp" | "fallback"}.
    - "webp": a real generated WebP thumbnail is served.
    - "fallback": no dedicated thumbnail; the original file URL is returned so
      the frontend can render it natively (react-pdf for PDFs, <video> for videos,
      <img> for images).
    Raises 404 for types without any renderable fallback (Office, audio, etc.).
    """
    mid = uuid.UUID(material_id)
    version = await get_material_thumbnail_info(db, mid, redis)
    if not version:
        raise AppError(404, "Material version not found")

    # 1. Prefer dedicated stored thumbnail
    target_key = version.get("thumbnail_key")
    content_type = "image/webp"
    is_dedicated = bool(target_key)

    # 2. Fallback to main file for types the browser can natively render inline
    #    (images, videos, PDFs). Audio, Office, and generic blobs are excluded
    #    because the browser cannot render them in an <img> / <video> thumbnail.
    if not target_key:
        file_mime = (version.get("file_mime_type") or "").lower()
        file_name = (version.get("file_name") or "").lower()
        is_pdf = file_mime == "application/pdf" or file_name.endswith(".pdf")
        is_image = file_mime.startswith("image/") or file_name.endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")
        )
        is_video = file_mime.startswith("video/") or file_name.endswith(
            (".mp4", ".webm", ".avi", ".mkv", ".mov")
        )

        if is_pdf or is_image or is_video:
            target_key = version["file_key"]
            content_type = (
                file_mime if file_mime and "/" in file_mime else "application/octet-stream"
            )
            if is_pdf and not file_mime:
                content_type = "application/pdf"
        else:
            raise AppError(404, "Thumbnail not available for this file type")

    thumb_filename = f"thumb_{version.get('file_name') or 'file'}.webp"
    url = await _presigned_url(
        target_key,
        redis,
        force_download=False,
        filename=thumb_filename,
        content_type=content_type,
    )
    return {
        "url": url,
        "thumbnail_type": "webp" if is_dedicated else "fallback",
    }


@router.get("/{material_id}/file")
async def stream_material_file(
    material_id: str,
    request: Request,
    user: ReadUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,  # type: ignore[type-arg]
) -> Any:
    """Redirect a read-authenticated client to an immutable presigned object URL."""
    if redis is None:
        redis = redis_client

    await rate_limit_downloads(request, user, db, redis)

    data = await get_material_with_version(db, material_id)
    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise NotFoundError("No file available")

    # S3/MinIO handles Range requests (206), which native media players require.
    file_mime = version.get("file_mime_type") or ""
    inline_safe = (
        file_mime.startswith("image/")
        or file_mime.startswith("video/")
        or file_mime.startswith("audio/")
        or file_mime == "application/pdf"
    )
    url = await _presigned_url(
        version["file_key"],
        redis,
        force_download=not inline_safe,
        filename=version.get("file_name"),
        content_type=file_mime or None,
    )

    await record_download(
        db,
        user.id,
        uuid.UUID(material_id),
        version["version_number"],
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

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
    data = await get_material_with_version(db, material_id)
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


@router.post("/{material_id}/favourite")
async def favourite_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    try:
        uid = uuid.UUID(material_id)
    except ValueError:
        raise BadRequestError(f"Invalid material ID: {material_id}")
    favourited = await toggle_favourite(db, user.id, uid)
    await db.commit()
    return {"favourited": favourited}


# ---------------------------------------------------------------------------
# Text-content endpoints (for inline text editing)
# ---------------------------------------------------------------------------


@router.get("/{material_id}/text-content", response_class=PlainTextResponse)
async def get_material_text_content(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PlainTextResponse:
    """Return the raw UTF-8 text of the material's current version.

    Works for both plain-text files and gzip-compressed text files (.gz).
    Only available for text-based MIME types.
    """
    data = await get_material_with_version(db, material_id)
    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise NotFoundError("No file available")

    mime = (version.get("file_mime_type") or "").lower()
    filename = (version.get("file_name") or "").lower()

    # Allow gzip-wrapped text files (e.g. original.md.gz)
    is_gzip_wrapped = mime == "application/gzip" or filename.endswith(".gz")

    if not is_gzip_wrapped and not _is_text_mime(mime) and mime not in _TEXT_READABLE_EXACT:
        raise BadRequestError("This file is not a text-based document and cannot be edited as text")

    raw_bytes = await read_full_object(version["file_key"])

    # Legacy gzip content is accepted only through the bounded decoder. Magic
    # bytes are authoritative so a disguised gzip stream cannot bypass limits.
    if is_gzip_wrapped or raw_bytes.startswith(_GZIP_MAGIC):
        raw_bytes = _decompress_gzip_text(raw_bytes)

    # Detect and strip UTF-8 BOM if present
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")

    return PlainTextResponse(content=text, media_type="text/plain; charset=utf-8")


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

    data = await get_material_with_version(db, material_id)
    version = data.get("current_version_info")
    if version is None:
        raise NotFoundError("No version found for this material")

    current_mime = (version.get("file_mime_type") or "").lower()
    current_name = version.get("file_name") or ""

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
        old_bytes = await read_full_object(version["file_key"])
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
        await redis_inst.zrem(f"quota:uploads:{user_id_str}", staging_quota_key)

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
        privileged=user_role in PRIVILEGED_ROLES,
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
