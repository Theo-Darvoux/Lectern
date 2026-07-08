"""compress_file_path: path-based dispatcher for file compression.

Routes by MIME type to the appropriate compression strategy.
SVG safety is always checked regardless of file size.
Fail-open: returns original path + size on any non-security error.
"""

import asyncio
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

from app.core.file_security._audio_video import (
    VIDEO_COMPRESS_THRESHOLD,
    _compress_video_path,
    _convert_to_opus_path,
)
from app.core.file_security._concurrency import _get_concurrency_guard
from app.core.file_security._image import _compress_image_path
from app.core.file_security._pdf import _compress_pdf_path
from app.core.file_security._svg import SvgSecurityError, _optimize_svg, check_svg_safety
from app.core.file_security._zip import _gzip_compress_path, _recompress_zip_path
from app.core.mimetypes import GZIP_MIME_TYPES, ZIP_MIME_TYPES

logger = logging.getLogger(__name__)

# Threshold above which we skip video compression (re-exported for process_upload)
__all__ = ["compress_file_path", "CompressResultPath", "VIDEO_COMPRESS_THRESHOLD"]

_COMPRESSION_SKIP_THRESHOLD = 10 * 1024  # 10 KiB — skip compression for tiny files


class CompressResultPath(NamedTuple):
    """Result of file compression attempt from a path."""

    path: Path
    size: int
    content_encoding: str | None
    mime_type: str


def _optimize_svg_to_path(file_path: Path, filename: str) -> Path:
    """Read, validate, optionally optimise, re-validate, and write an SVG to a temp file.

    Returns a new temp path if scour improved things; otherwise returns *file_path*.
    Raises SvgSecurityError on any security violation.
    """
    svg_bytes = file_path.read_bytes()
    check_svg_safety(svg_bytes, filename)
    new_path = None
    try:
        optimised = _optimize_svg(svg_bytes)
        if optimised and len(optimised) < len(svg_bytes):
            new_svg = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
            new_svg.write(optimised)
            new_svg.close()
            new_path = Path(new_svg.name)
            # Re-verify the optimized markup
            check_svg_safety(new_path.read_bytes(), filename)
            return new_path
    except SvgSecurityError:
        if new_path is not None:
            new_path.unlink(missing_ok=True)
        raise
    except Exception as svg_err:
        logger.debug("scour SVG optimization failed or skipped: %s", svg_err)
        if new_path is not None:
            new_path.unlink(missing_ok=True)
    return file_path


async def compress_file_path(
    file_path: Path, mime_type: str, filename: str = "", config: dict[str, Any] | None = None
) -> CompressResultPath:
    """Compress *file_path* for storage efficiency. Fail-open on non-security errors.

    Dispatches by MIME type:
    - image/svg+xml: safety check + scour + gzip (always, regardless of size threshold)
    - application/pdf: Ghostscript profile (configurable, default /prepress)
    - video/mp4, video/webm: FFmpeg re-encode via configurable compression profiles (default 'heavy')
    - audio/*: FFmpeg to Opus conversion
    - ZIP formats (DOCX/XLSX/PPTX/ODT/ODS/EPUB): content-aware re-compression
    - text/* + gzip-eligible MIME types: gzip level 9

    SVG files are validated against the security allowlist both before and after
    scour optimisation. SvgSecurityError propagates (caller converts to 400).
    """
    file_size = file_path.stat().st_size

    try:
        # SVG safety check must happen regardless of compression skip threshold
        if mime_type == "image/svg+xml":
            source_path = await asyncio.to_thread(_optimize_svg_to_path, file_path, filename)

            try:
                compressed = await asyncio.to_thread(_gzip_compress_path, source_path)
                # _gzip_compress_path returns source_path unchanged when gzip does not
                # reduce the file size.  Only set content_encoding when a new .gz file
                # was actually produced — otherwise we'd store plain bytes in R2 with
                # ContentEncoding: gzip, causing the Worker to feed raw SVG into
                # DecompressionStream and serve garbage to the browser.
                gzip_helped = compressed != source_path
                if gzip_helped and source_path != file_path:
                    source_path.unlink(missing_ok=True)
                return CompressResultPath(
                    compressed,
                    compressed.stat().st_size,
                    "gzip" if gzip_helped else None,
                    mime_type,
                )
            except Exception as gz_err:
                logger.warning("SVG gzip failed: %s", gz_err)
                if source_path != file_path:
                    return CompressResultPath(
                        source_path, source_path.stat().st_size, None, mime_type
                    )
                return CompressResultPath(file_path, file_size, None, mime_type)

        # Skip compression entirely for very small files
        if file_size < _COMPRESSION_SKIP_THRESHOLD:
            return CompressResultPath(file_path, file_size, None, mime_type)

        if mime_type == "application/pdf":
            async with _get_concurrency_guard("image"):
                compressed = await _compress_pdf_path(file_path, config=config)
            return CompressResultPath(compressed, compressed.stat().st_size, None, mime_type)

        if mime_type in ("video/mp4", "video/webm"):
            ext = ".mp4" if mime_type == "video/mp4" else ".webm"
            compressed = await _compress_video_path(file_path, ext, config=config)
            return CompressResultPath(compressed, compressed.stat().st_size, None, mime_type)

        if mime_type.startswith("audio/"):
            opus_path = await _convert_to_opus_path(file_path)
            new_mime = "audio/webm" if opus_path != file_path else mime_type
            return CompressResultPath(opus_path, opus_path.stat().st_size, None, new_mime)

        if mime_type in ZIP_MIME_TYPES:
            try:
                compressed = await asyncio.to_thread(_recompress_zip_path, file_path)
                return CompressResultPath(compressed, compressed.stat().st_size, None, mime_type)
            except (zipfile.BadZipFile, ValueError) as exc:
                logger.warning("ZIP recompression skipped for %s: %s", filename, exc)
            return CompressResultPath(file_path, file_path.stat().st_size, None, mime_type)

        if mime_type.startswith("text/") or mime_type in GZIP_MIME_TYPES:
            compressed = await asyncio.to_thread(_gzip_compress_path, file_path)
            if compressed != file_path:
                return CompressResultPath(compressed, compressed.stat().st_size, "gzip", mime_type)
            return CompressResultPath(file_path, file_path.stat().st_size, None, mime_type)

        if mime_type.startswith("image/") and mime_type != "image/svg+xml":
            async with _get_concurrency_guard("image"):
                compressed = await asyncio.to_thread(_compress_image_path, file_path)

            # If the file was successfully compressed/converted and it's not a GIF, it's now a WEBP
            new_mime = (
                "image/webp"
                if (compressed != file_path and mime_type != "image/gif")
                else mime_type
            )
            return CompressResultPath(compressed, compressed.stat().st_size, None, new_mime)

    except SvgSecurityError:
        raise
    except Exception as e:
        logger.warning("Compression failed for path %s (%s): %s", filename, mime_type, e)

    return CompressResultPath(file_path, file_path.stat().st_size, None, mime_type)
