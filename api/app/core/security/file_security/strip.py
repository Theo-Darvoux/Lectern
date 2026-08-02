"""Path-based dispatcher for metadata removal.

Images, PDFs, and Office documents fail closed. Audio/video stripping remains
best effort because those processors are optional privacy enhancements rather
than the upload security boundary.
"""

import logging
from pathlib import Path

from app.core.media.mimetypes import OLE2_MIME_TYPES, ZIP_MIME_TYPES
from app.core.security.file_security._audio_video import (
    _strip_audio_from_path,
    _strip_video_from_path,
)
from app.core.security.file_security._concurrency import (
    _shielded_to_thread,
    image_guard,
)
from app.core.security.file_security._image import _strip_image_from_path
from app.core.security.file_security._office import _strip_ole2_from_path, _strip_ooxml_from_path
from app.core.security.file_security._pdf import _strip_pdf_from_path
from app.core.security.file_security.errors import SanitizationError

logger = logging.getLogger(__name__)


def _must_fail_closed(mime_type: str) -> bool:
    return (
        mime_type.startswith("image/")
        or mime_type == "application/pdf"
        or mime_type in OLE2_MIME_TYPES
        or mime_type in ZIP_MIME_TYPES
    )


async def strip_metadata_file(file_path: Path, mime_type: str) -> Path:
    """Remove metadata and reject files that cannot be safely sanitized."""
    try:
        if mime_type == "image/svg+xml":
            # SVG is validated by the dedicated SVG security stage.
            return file_path
        if mime_type.startswith("image/"):
            async with image_guard():
                return await _shielded_to_thread(_strip_image_from_path, file_path)
        if mime_type == "application/pdf":
            async with image_guard():
                return await _shielded_to_thread(_strip_pdf_from_path, file_path)
        if mime_type.startswith("video/"):
            return await _strip_video_from_path(file_path, mime_type)
        if mime_type.startswith("audio/"):
            return await _shielded_to_thread(_strip_audio_from_path, file_path, mime_type)
        if mime_type in OLE2_MIME_TYPES:
            return await _strip_ole2_from_path(file_path, mime_type)
        if mime_type in ZIP_MIME_TYPES:
            return await _strip_ooxml_from_path(file_path, mime_type)
    except SanitizationError:
        raise
    except ValueError as exc:
        # Legacy sanitizer helpers still use ValueError for deliberate rejection.
        raise SanitizationError(str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Failed to strip metadata from path %s (%s): %s",
            file_path,
            mime_type,
            exc,
        )
        if _must_fail_closed(mime_type):
            raise SanitizationError(
                f"Failed to sanitize {mime_type} file for privacy and safety."
            ) from exc

    return file_path
