"""Single source of truth for configured upload-size limits."""

from collections.abc import Mapping
from typing import Any

from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.core.common.upload_errors import UploadErrorCode

_MIB = 1024 * 1024


def _configured_bytes(
    key: str,
    fallback_mb: int,
    config: Mapping[str, Any] | None,
) -> int:
    value = config.get(key) if config is not None else None
    return int(value if value is not None else fallback_mb) * _MIB


_TEXT_APPLICATION_MIMES = frozenset(
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


def upload_size_limit(
    mime_type: str,
    config: Mapping[str, Any] | None = None,
) -> tuple[int, bool]:
    """Return ``(limit_bytes, is_global_fallback)`` for a MIME type."""
    normalized = mime_type.split(";", 1)[0].strip().lower()
    text_limit = _configured_bytes("max_text_size_mb", settings.max_text_size_mb, config)
    if normalized in _TEXT_APPLICATION_MIMES:
        return text_limit, False

    document_limit = _configured_bytes(
        "max_document_size_mb", settings.max_document_size_mb, config
    )
    office_limit = _configured_bytes("max_office_size_mb", settings.max_office_size_mb, config)

    exact_limits = {
        "image/svg+xml": _configured_bytes("max_svg_size_mb", settings.max_svg_size_mb, config),
        "application/pdf": document_limit,
        "application/epub+zip": document_limit,
        "image/vnd.djvu": document_limit,
        "application/msword": office_limit,
    }
    if normalized in exact_limits:
        return exact_limits[normalized], False

    prefix_limits = (
        ("image/", _configured_bytes("max_image_size_mb", settings.max_image_size_mb, config)),
        ("audio/", _configured_bytes("max_audio_size_mb", settings.max_audio_size_mb, config)),
        ("video/", _configured_bytes("max_video_size_mb", settings.max_video_size_mb, config)),
        ("text/", text_limit),
        ("application/vnd.openxmlformats", office_limit),
        ("application/vnd.ms-", office_limit),
    )

    for prefix, limit in prefix_limits:
        if normalized.startswith(prefix):
            return limit, False

    return _configured_bytes("max_file_size_mb", settings.max_file_size_mb, config), True


def enforce_upload_size_limit(
    mime_type: str,
    size_bytes: int,
    config: Mapping[str, Any] | None = None,
) -> None:
    """Raise BadRequestError if ``size_bytes`` exceeds the configured MIME-specific limit."""
    limit, is_global = upload_size_limit(mime_type, config)
    if size_bytes <= limit:
        return

    mb = limit // _MIB
    if is_global:
        msg = f"File size {size_bytes // _MIB} MiB exceeds the global limit of {mb} MiB."
    else:
        msg = f"File size exceeds the {mb} MiB limit for this file type."
    raise BadRequestError(msg, code=UploadErrorCode.FILE_TOO_LARGE)
