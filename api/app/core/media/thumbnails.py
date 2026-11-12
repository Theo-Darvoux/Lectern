"""Which stored files get a generated thumbnail, and which can be previewed natively.

Both the worker (to pick a renderer) and the API (to pick a fallback when no
thumbnail exists) classify files here, so the two never disagree.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath

from app.core.media.mimetypes import ALLOWED_FORMAT_REGISTRY


class ThumbnailKind(StrEnum):
    IMAGE = "image"
    SVG = "svg"
    DJVU = "djvu"
    VIDEO = "video"
    AUDIO = "audio"
    PDF = "pdf"
    OFFICE = "office"
    EPUB = "epub"


# LibreOffice picks its import filter from the file extension, so the renderer
# copies the document under the canonical suffix for its MIME type.
OFFICE_MIME_SUFFIXES: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
}

# Kinds whose original file an <img>, <video> or pdf.js can render when no
# generated thumbnail exists. DjVu, audio, Office and EPUB cannot.
_NATIVE_PREVIEW_KINDS = frozenset(
    {ThumbnailKind.IMAGE, ThumbnailKind.SVG, ThumbnailKind.VIDEO, ThumbnailKind.PDF}
)

_GENERIC_MIMES = frozenset({"", "application/octet-stream"})


def _normalize(mime_type: str | None) -> str:
    return (mime_type or "").split(";", 1)[0].strip().lower()


def effective_mime(mime_type: str | None, file_name: str | None = None) -> str:
    """Return the stored MIME type, or the extension's canonical one when it is generic.

    Detected MIME types are authoritative. The extension only fills in legacy
    rows that were stored without a specific type.
    """
    mime = _normalize(mime_type)
    if mime not in _GENERIC_MIMES or not file_name:
        return mime
    candidates = ALLOWED_FORMAT_REGISTRY.get(PurePosixPath(file_name).suffix.lower(), [])
    return candidates[0] if candidates else mime


def thumbnail_kind(mime_type: str | None, file_name: str | None = None) -> ThumbnailKind | None:
    """Return the renderer for a file, or None when it never gets a thumbnail."""
    mime = effective_mime(mime_type, file_name)
    if mime == "image/svg+xml":
        return ThumbnailKind.SVG
    if mime == "image/vnd.djvu":
        return ThumbnailKind.DJVU
    if mime.startswith("image/"):
        return ThumbnailKind.IMAGE
    if mime.startswith("video/"):
        return ThumbnailKind.VIDEO
    if mime.startswith("audio/"):
        return ThumbnailKind.AUDIO
    if mime == "application/pdf":
        return ThumbnailKind.PDF
    if mime == "application/epub+zip":
        return ThumbnailKind.EPUB
    if mime in OFFICE_MIME_SUFFIXES:
        return ThumbnailKind.OFFICE
    return None


def has_native_preview(mime_type: str | None, file_name: str | None = None) -> bool:
    """Whether the browser can render the original file as a card preview."""
    return thumbnail_kind(mime_type, file_name) in _NATIVE_PREVIEW_KINDS
