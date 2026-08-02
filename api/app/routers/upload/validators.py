"""Upload validation helpers: filename sanitization, MIME correction, size checks."""

import os
import re
from typing import Any

from app.core.common.exceptions import BadRequestError
from app.core.common.upload_errors import UploadErrorCode
from app.core.common.upload_limits import upload_size_limit
from app.core.media.mimetypes import MimeRegistry

_MAX_FILENAME_LENGTH = 255


def _check_per_type_size(mime_type: str, size: int, config: dict[str, Any] | None = None) -> None:
    """Raise BadRequestError if ``size`` exceeds the configured MIME-specific limit."""
    limit, is_global = upload_size_limit(mime_type, config)
    if size <= limit:
        return

    mb = limit // (1024 * 1024)
    if is_global:
        msg = f"File size {size // (1024 * 1024)} MiB exceeds the global limit of {mb} MiB."
    else:
        msg = f"File size exceeds the {mb} MiB limit for this file type."
    raise BadRequestError(msg, code=UploadErrorCode.FILE_TOO_LARGE)


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
        raise BadRequestError("Invalid filename", code=UploadErrorCode.TYPE_NOT_ALLOWED)
    if len(safe_name) > _MAX_FILENAME_LENGTH:
        raise BadRequestError(
            f"Filename too long ({len(safe_name)} chars, max {_MAX_FILENAME_LENGTH}).",
            code=UploadErrorCode.FILENAME_TOO_LONG,
        )
    ext = MimeRegistry.get_extension(safe_name)
    if not MimeRegistry.is_supported_extension(ext, allowed=allowed_extensions):
        raise BadRequestError(
            f"File extension '{ext}' is not supported.",
            code=UploadErrorCode.TYPE_NOT_ALLOWED,
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
            code=UploadErrorCode.TYPE_NOT_ALLOWED,
        )

    allowed_mimes = MimeRegistry.get_allowed_mimes_for_extension(ext)  # type: ignore[assignment]

    if allowed_mimes and detected_mime not in allowed_mimes:
        raise BadRequestError(
            f"File extension '{ext}' does not match detected type '{detected_mime}'.",
            code=UploadErrorCode.MIME_MISMATCH,
        )

    if not ext:
        new_ext = MimeRegistry.get_canonical_extension(detected_mime)
        if new_ext:
            return f"{safe_name}{new_ext}", new_ext

    return safe_name, ext
