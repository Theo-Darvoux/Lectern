"""Upload validation helpers: filename sanitization, MIME correction, size checks."""

import os
import re
from typing import Any

from app.config import settings
from app.core.exceptions import BadRequestError
from app.core.mimetypes import MimeRegistry
from app.core.upload_errors import (
    ERR_FILE_TOO_LARGE,
    ERR_FILENAME_TOO_LONG,
    ERR_MIME_MISMATCH,
    ERR_TYPE_NOT_ALLOWED,
)

_MAX_FILENAME_LENGTH = 255


def _mb(key: str, fallback: int, config: dict[str, Any] | None) -> int:
    """Return the byte limit for *key*, preferring config over the settings default."""
    v = config.get(key) if config else None
    return (v if v is not None else fallback) * 1024 * 1024


def _build_limits(config: dict[str, Any] | None) -> dict[str, int]:
    doc = _mb("max_document_size_mb", settings.max_document_size_mb, config)
    office = _mb("max_office_size_mb", settings.max_office_size_mb, config)
    return {
        "image/svg+xml": _mb("max_svg_size_mb", settings.max_svg_size_mb, config),
        "image/": _mb("max_image_size_mb", settings.max_image_size_mb, config),
        "audio/": _mb("max_audio_size_mb", settings.max_audio_size_mb, config),
        "video/": _mb("max_video_size_mb", settings.max_video_size_mb, config),
        "application/pdf": doc,
        "application/epub+zip": doc,
        "image/vnd.djvu": doc,
        "text/": _mb("max_text_size_mb", settings.max_text_size_mb, config),
        "application/vnd.openxmlformats": office,
        "application/msword": office,
        "application/vnd.ms-": office,
    }


def _check_per_type_size(mime_type: str, size: int, config: dict[str, Any] | None = None) -> None:
    """Raise BadRequestError if ``size`` exceeds the applicable limit for ``mime_type``.

    Category-specific limits (e.g. 500MB for video) take precedence over the
    global default.
    """
    limits = _build_limits(config)
    global_limit = _mb("max_file_size_mb", settings.max_file_size_mb, config)

    # 1. Exact MIME match first, then prefix match
    limit = limits.get(mime_type)
    if limit is None:
        for prefix, cap in limits.items():
            if prefix.endswith("/") and mime_type.startswith(prefix):
                limit = cap
                break
            if not prefix.endswith("/") and mime_type.startswith(prefix):
                limit = cap
                break

    # 2. Fallback to global limit
    is_global = False
    if limit is None:
        limit = global_limit
        is_global = True

    # 3. Validate
    if size > limit:
        mb = limit // (1024 * 1024)
        if is_global:
            msg = f"File size {size // (1024 * 1024)} MiB exceeds the global limit of {mb} MiB."
        else:
            msg = f"File size exceeds the {mb} MiB limit for this file type."
        raise BadRequestError(msg, code=ERR_FILE_TOO_LARGE)


def _sanitize_filename(raw: str) -> str:
    """Sanitize a filename: strip control chars, Unicode trickery, and path/shell-unsafe chars.

    Preserved: accents, French letters, and the characters ' " - _ * $ = } ) ] @ [ ( { # \u20ac.
    Stripped \u2192 underscore: whitespace, %, &, backslash, < > ? / ! : + ` | ^ ~.
    """
    name = os.path.basename(raw)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"[\u200b-\u200f\u2028-\u202f\u2060\ufeff]", "", name)
    name = re.sub(r"[\s%&\\<>?/!:+`|^~#*]", "_", name)

    parts = name.rsplit(".", 1)
    if len(parts) > 1:
        base = re.sub(r"_{3,}", "_", parts[0]).strip("_.")
        ext = re.sub(r"_{3,}", "_", parts[1]).strip("_.")
        if parts[0] and not base:
            return ""
        return f"{base}.{ext}" if base else f".{ext}"
    else:
        return re.sub(r"_{3,}", "_", name).strip("_.")


def _validate_filename(
    raw: str, allowed_extensions: set[str] | frozenset[str] | None = None
) -> tuple[str, str]:
    """Return (safe_name, ext) or raise BadRequestError with structured code."""
    safe_name = _sanitize_filename(raw or "unnamed")
    if not safe_name:
        raise BadRequestError("Invalid filename", code=ERR_TYPE_NOT_ALLOWED)
    if len(safe_name) > _MAX_FILENAME_LENGTH:
        raise BadRequestError(
            f"Filename too long ({len(safe_name)} chars, max {_MAX_FILENAME_LENGTH}).",
            code=ERR_FILENAME_TOO_LONG,
        )
    ext = MimeRegistry.get_extension(safe_name)
    if not MimeRegistry.is_supported_extension(ext, allowed=allowed_extensions):
        raise BadRequestError(
            f"File extension '{ext}' is not supported.",
            code=ERR_TYPE_NOT_ALLOWED,
        )
    return safe_name, ext


def _apply_mime_correction(
    safe_name: str,
    detected_mime: str,
    ext: str,
    allowed_mimes: set[str] | frozenset[str] | None = None,
) -> tuple[str, str]:
    """Reject uploads where magic bytes conflict with a known extension's accepted MIME types."""
    if not MimeRegistry.is_allowed_mime(detected_mime, allowed=allowed_mimes):
        raise BadRequestError(
            f"Detected file type '{detected_mime}' is not allowed.",
            code=ERR_TYPE_NOT_ALLOWED,
        )

    allowed_mimes = MimeRegistry.get_allowed_mimes_for_extension(ext)  # type: ignore[assignment]

    if allowed_mimes and detected_mime not in allowed_mimes:
        raise BadRequestError(
            f"File extension '{ext}' does not match detected type '{detected_mime}'.",
            code=ERR_MIME_MISMATCH,
        )

    if not ext:
        new_ext = MimeRegistry.get_canonical_extension(detected_mime)
        if new_ext:
            return f"{safe_name}{new_ext}", new_ext

    return safe_name, ext
