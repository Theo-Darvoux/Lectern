from __future__ import annotations

import gzip
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AppError, BadRequestError
from app.core.redis import get_redis
from app.dependencies.auth import CurrentUser, security
from app.dependencies.rate_limit import rate_limit_downloads
from app.models.user import User
from app.schemas.material import MaterialDetail, MaterialVersionOut
from app.services.audit import record_download
from app.services.material import (
    check_material_access,
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
_TEXT_EDIT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB cap on raw text body

router = APIRouter(prefix="/api/materials", tags=["materials"])


def _is_text_mime(mime: str) -> bool:
    """Return True if this MIME type can be represented as editable UTF-8 text."""
    m = (mime or "").lower()
    if any(m.startswith(p) for p in _TEXT_MIME_PREFIXES):
        return True
    return m in _TEXT_MIME_EXACT


@router.get("/{material_id}", response_model=MaterialDetail)
async def get_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MaterialDetail:
    data = await get_material_with_version(db, material_id, current_user_id=user.id)
    if user is not None:
        check_material_access(user.id, data)
    return MaterialDetail.model_validate(data)


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
    check_material_access(user.id, data)
    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("No file available for download")

    from app.core.storage import generate_presigned_get_url, generate_presigned_get_url_cached

    if redis is not None:
        url = await generate_presigned_get_url_cached(
            version["file_key"],
            redis=redis,
            filename=version.get("file_name"),
            content_type=version.get("file_mime_type"),
        )
    else:
        url = await generate_presigned_get_url(
            version["file_key"],
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
    from app.services.material import check_material_access, get_material_with_version

    data = await get_material_with_version(db, material_id)
    check_material_access(user.id, data)

    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("No file available for preview")

    from app.core.storage import generate_presigned_get_url, generate_presigned_get_url_cached

    # Images, PDFs, and Videos are safe to render inline; all other types are forced
    # to download so the browser never executes or parses unknown content.
    file_mime = version.get("file_mime_type") or ""
    inline_safe = (
        file_mime.startswith("image/")
        or file_mime.startswith("video/")
        or file_mime == "application/pdf"
    )
    force_download = not inline_safe
    file_key = version["file_key"]

    # Use Redis-cached URL so Cloudflare CDN sees the same URL across requests
    # and can serve the file from the edge rather than the slow R2 origin.
    if redis is not None:
        url = await generate_presigned_get_url_cached(
            file_key,
            redis=redis,
            force_download=force_download,
            filename=version.get("file_name"),
            content_type=version.get("file_mime_type"),
        )
    else:
        url = await generate_presigned_get_url(
            file_key,
            force_download=force_download,
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

    from app.core.storage import generate_presigned_get_url, generate_presigned_get_url_cached

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
    if redis is not None:
        url = await generate_presigned_get_url_cached(
            target_key,
            redis=redis,
            force_download=False,
            filename=thumb_filename,
            content_type=content_type,
        )
    else:
        url = await generate_presigned_get_url(
            target_key,
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
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    token: Annotated[str | None, Query()] = None,
    redis: Annotated[Redis | None, Depends(get_redis)] = None,  # type: ignore[type-arg]
) -> Any:
    from app.core.exceptions import NotFoundError, UnauthorizedError

    # Manual auth: accept either Authorization: Bearer header OR ?token= query param.
    effective_user: User | None = None

    # Ensure Redis is available for auth checks and rate limiting.
    if redis is None:
        from app.core.redis import redis_client

        redis = redis_client

    if user is not None:
        try:
            from app.dependencies.auth import get_user_from_token

            effective_user = await get_user_from_token(db, redis, user.credentials)
        except Exception:
            pass

    if not effective_user and token:
        try:
            from app.dependencies.auth import get_user_from_token

            effective_user = await get_user_from_token(db, redis, token)
        except Exception:
            pass

    if not effective_user:
        raise UnauthorizedError()

    from app.dependencies.rate_limit import rate_limit_downloads

    await rate_limit_downloads(request, effective_user, db, redis)

    data = await get_material_with_version(db, material_id)
    check_material_access(effective_user.id, data)

    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise NotFoundError("No file available")

    from app.core.storage import generate_presigned_get_url, generate_presigned_get_url_cached

    # Redirect to presigned URL. S3/MinIO handles Range requests (206) perfectly,
    # which is required for browser media players to seek and parse metadata.
    if redis is not None:
        url = await generate_presigned_get_url_cached(
            version["file_key"],
            redis=redis,
            filename=version.get("file_name"),
            content_type=version.get("file_mime_type"),
        )
    else:
        url = await generate_presigned_get_url(
            version["file_key"],
            filename=version.get("file_name"),
            content_type=version.get("file_mime_type"),
        )

    await record_download(
        db,
        effective_user.id,
        uuid.UUID(material_id),
        version["version_number"],
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    return RedirectResponse(url=url, status_code=302)


@router.get("/{material_id}/versions", response_model=list[MaterialVersionOut])
async def list_versions(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MaterialVersionOut]:
    versions = await get_material_versions(db, material_id)
    return [MaterialVersionOut.model_validate(v) for v in versions]


@router.get("/{material_id}/versions/{version_number}", response_model=MaterialVersionOut)
async def get_version(
    material_id: str,
    version_number: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MaterialVersionOut:
    version = await get_material_version(db, material_id, version_number)
    return MaterialVersionOut.model_validate(version)


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
    check_material_access(user.id, data)

    version = await get_material_version(db, material_id, version_number)
    if not version.file_key:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("No file available for download")

    from app.core.storage import generate_presigned_get_url, generate_presigned_get_url_cached

    if redis is not None:
        url = await generate_presigned_get_url_cached(
            version.file_key,
            redis=redis,
            filename=version.file_name,
            content_type=version.file_mime_type,
        )
    else:
        url = await generate_presigned_get_url(
            version.file_key,
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


@router.get("/{material_id}/attachments", response_model=list[MaterialDetail])
async def list_attachments(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MaterialDetail]:
    attachments = await get_material_attachments(db, material_id, current_user_id=user.id)
    return [MaterialDetail.model_validate(a) for a in attachments]


@router.post("/{material_id}/view")
async def view_material(
    material_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
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
    from app.core.exceptions import BadRequestError, NotFoundError
    from app.core.storage import read_full_object

    data = await get_material_with_version(db, material_id)
    check_material_access(user.id, data)

    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise NotFoundError("No file available")

    mime = (version.get("file_mime_type") or "").lower()
    filename = (version.get("file_name") or "").lower()

    # Allow gzip-wrapped text files (e.g. original.md.gz)
    is_gzip_wrapped = mime == "application/gzip" or filename.endswith(".gz")

    if not is_gzip_wrapped and not _is_text_mime(mime):
        raise BadRequestError("This file is not a text-based document and cannot be edited as text")

    raw_bytes = await read_full_object(version["file_key"])

    # Decompress if explicitly wrapped OR if bytes look like GZIP (X12 fix)
    if is_gzip_wrapped or raw_bytes.startswith(b"\x1f\x8b"):
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except Exception as exc:
            # If magic number was a false positive, we just fall through
            if is_gzip_wrapped:
                raise BadRequestError(f"Failed to decompress file: {exc}") from exc

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
) -> dict[str, Any]:
    """Accept raw UTF-8 text, gzip-compress it server-side, store to object storage.

    Creates a clean Upload row so the returned file_key passes PR validation.
    Returns ``{ file_key, file_name, file_size, file_mime_type }`` ready
    to be included in an ``edit_material`` PR operation.
    """
    from app.core.exceptions import BadRequestError, NotFoundError
    from app.core.storage import upload_file as storage_upload_file
    from app.models.upload import Upload

    data = await get_material_with_version(db, material_id)
    check_material_access(user.id, data)

    version = data.get("current_version_info")
    if version is None:
        raise NotFoundError("No version found for this material")

    current_mime = (version.get("file_mime_type") or "").lower()
    current_name = version.get("file_name") or ""

    # Strip any previous .gz suffix to derive the "logical" original name
    logical_name = current_name[:-3] if current_name.endswith(".gz") else current_name

    is_gzip_wrapped = current_mime == "application/gzip" or current_name.endswith(".gz")

    # Determine inner MIME type for validation
    import mimetypes as _mimetypes

    if is_gzip_wrapped:
        guessed, _ = _mimetypes.guess_type(logical_name)
        check_mime = guessed or "text/plain"
    else:
        check_mime = current_mime

    if not _is_text_mime(check_mime) and not is_gzip_wrapped:
        raise BadRequestError("Cannot save text content for a non-text file")

    # Compute text diff
    import difflib

    from app.core.storage import read_full_object

    try:
        old_bytes = await read_full_object(version["file_key"])
        if is_gzip_wrapped:
            old_bytes = gzip.decompress(old_bytes)
        if old_bytes.startswith(b"\xef\xbb\xbf"):
            old_bytes = old_bytes[3:]
        try:
            old_text = old_bytes.decode("utf-8")
        except UnicodeDecodeError:
            old_text = old_bytes.decode("latin-1")
    except Exception:
        old_text = ""

    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            body.splitlines(),
            fromfile=current_name,
            tofile=logical_name,
            lineterm="",
        )
    )
    diff_text = "```diff\n" + "\n".join(diff_lines) + "\n```" if diff_lines else ""

    # Encode without compression
    raw_bytes = body.encode("utf-8")

    # Build deterministic storage key scoped to the user
    upload_id = str(uuid.uuid4())
    file_key = f"uploads/{user.id}/{upload_id}/{logical_name}"
    file_size = len(raw_bytes)

    # Upload to object storage
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
    await db.commit()

    return {
        "file_key": file_key,
        "file_name": logical_name,
        "file_size": file_size,
        "file_mime_type": check_mime,
        "diff": diff_text,
    }
