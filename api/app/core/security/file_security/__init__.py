"""file_security — file safety, metadata stripping, and compression.

This package is the drop-in replacement for the former monolithic
``app/core/file_security.py``. Its supported public API is re-exported here;
format-specific implementation helpers remain in their defining modules.

Public API (path-based, production):
    strip_metadata_file   — remove metadata from a file on disk
    compress_file_path    — compress a file on disk
    CompressResultPath    — NamedTuple returned by compress_file_path
    check_pdf_safety      — structural PDF safety validation
    check_svg_safety      — SVG allowlist-based safety check
    check_svg_safety_stream — stream variant for SVG safety check
    SvgSecurityError      — exception raised on SVG violation
    get_uncompressed_size — safe ZIP central-directory size query
    run_managed_subprocess— subprocess with global concurrency guard
"""

# ── Audio / Video ─────────────────────────────────────────────────────────────
from app.core.security.file_security._audio_video import VIDEO_COMPRESS_THRESHOLD

# ── Concurrency ───────────────────────────────────────────────────────────────
from app.core.security.file_security._concurrency import run_managed_subprocess

# ── Image ─────────────────────────────────────────────────────────────────────
from app.core.security.file_security._image import MAX_GIF_FRAMES, MAX_GIF_TOTAL_PIXELS

# ── PDF ───────────────────────────────────────────────────────────────────────
from app.core.security.file_security._pdf import check_pdf_safety

# ── SVG ───────────────────────────────────────────────────────────────────────
from app.core.security.file_security._svg import (
    SvgSecurityError,
    check_svg_safety,
    check_svg_safety_stream,
)

# ── ZIP / Gzip ────────────────────────────────────────────────────────────────
from app.core.security.file_security._zip import get_uncompressed_size

# ── Compress dispatcher ───────────────────────────────────────────────────────
from app.core.security.file_security.compress import CompressResultPath, compress_file_path

# ── Strip dispatcher ──────────────────────────────────────────────────────────
from app.core.security.file_security.strip import strip_metadata_file

__all__ = [
    "strip_metadata_file",
    "compress_file_path",
    "CompressResultPath",
    "check_pdf_safety",
    "check_svg_safety",
    "check_svg_safety_stream",
    "SvgSecurityError",
    "get_uncompressed_size",
    "run_managed_subprocess",
    "VIDEO_COMPRESS_THRESHOLD",
    "MAX_GIF_FRAMES",
    "MAX_GIF_TOTAL_PIXELS",
]
