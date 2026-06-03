"""strip_metadata_file: path-based dispatcher for metadata removal.

Routes by MIME type to the appropriate format-specific stripper.
Fail-closed for high-risk types (image, PDF); fail-open for others.

Helper functions are resolved through the ``app.core.file_security`` package
namespace at call time so existing test patches targeting that namespace
are correctly intercepted.
"""

import asyncio
import logging
from pathlib import Path

from app.core.file_security._audio_video import _strip_audio_from_path, _strip_video_from_path
from app.core.file_security._concurrency import _get_concurrency_guard
from app.core.file_security._image import _strip_image_from_path
from app.core.file_security._office import _strip_ole2_from_path, _strip_ooxml_from_path
from app.core.file_security._pdf import _strip_pdf_from_path
from app.core.mimetypes import OLE2_MIME_TYPES, ZIP_MIME_TYPES

logger = logging.getLogger(__name__)


async def strip_metadata_file(file_path: Path, mime_type: str) -> Path:
    """Remove PII and technical metadata from files directly on disk.

    Returns the Path to the clean file (may be the same path or a newly created temp file).

    This is 'fail-closed' for high-risk types (Images, PDFs): if stripping fails,
    a ValueError is raised to reject the upload. For other types, it fails open
    to the original path.
    """
    try:
        if mime_type.startswith("image/"):
            async with _get_concurrency_guard("image"):
                return await asyncio.to_thread(_strip_image_from_path, file_path)
        elif mime_type == "application/pdf":
            return await asyncio.to_thread(_strip_pdf_from_path, file_path)
        elif mime_type.startswith("video/"):
            return await _strip_video_from_path(file_path, mime_type)
        elif mime_type.startswith("audio/"):
            return await asyncio.to_thread(_strip_audio_from_path, file_path, mime_type)
        elif mime_type in OLE2_MIME_TYPES:
            return await _strip_ole2_from_path(file_path)
        elif mime_type in ZIP_MIME_TYPES:
            return await _strip_ooxml_from_path(file_path)
    except ValueError:
        # Propagation: macro detection or deliberate security rejections
        raise
    except Exception as e:
        logger.error("Failed to strip metadata from path %s (%s): %s", file_path, mime_type, e)
        # Fail-closed for high-risk types
        if mime_type.startswith("image/") or mime_type == "application/pdf":
            raise ValueError(
                f"Failed to sanitize {mime_type} file for privacy. Please ensure the file is valid and try again."
            ) from e
        # Fail-open for others (original path)

    return file_path
