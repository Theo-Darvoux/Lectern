"""file_security — file safety, metadata stripping, and compression.

This package is the drop-in replacement for the former monolithic
``app/core/file_security.py``. All public symbols are re-exported here so
existing imports remain valid without modification.

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

# Re-export stdlib modules that tests mock at the package namespace level.

# ── Audio / Video ─────────────────────────────────────────────────────────────
from app.core.file_security._audio_video import (
    VIDEO_COMPRESS_THRESHOLD,
    _build_video_codec_args,  # noqa: F401
    _compress_video_path,  # noqa: F401
    _convert_to_opus_path,  # noqa: F401
    _strip_audio_from_path,  # noqa: F401
    _strip_video_from_path,  # noqa: F401
)

# ── Concurrency ───────────────────────────────────────────────────────────────
from app.core.file_security._concurrency import (
    _get_concurrency_guard,  # noqa: F401
    run_managed_subprocess,
)

# ── Image ─────────────────────────────────────────────────────────────────────
from app.core.file_security._image import (
    MAX_GIF_FRAMES,
    MAX_GIF_TOTAL_PIXELS,
    _compress_image_path,  # noqa: F401
    _save_compressed_image,  # noqa: F401
    _save_stripped_image,  # noqa: F401
    _strip_gif_to_dest,  # noqa: F401
    _strip_image_from_path,  # noqa: F401
    _strip_image_metadata,  # noqa: F401
)

# ── Office ────────────────────────────────────────────────────────────────────
from app.core.file_security._office import (
    _OLE2_AUTO_EXEC,  # noqa: F401
    _check_ole2_macros,  # noqa: F401
    _scan_vba_for_autoexec,  # noqa: F401
    _strip_ole2_from_path,  # noqa: F401
    _strip_ooxml_from_path,  # noqa: F401
)

# ── PDF ───────────────────────────────────────────────────────────────────────
from app.core.file_security._pdf import (
    _PDF_DANGEROUS_ACTION_KEYS,  # noqa: F401
    _apply_pdf_security_strip,  # noqa: F401
    _compress_pdf_path,  # noqa: F401
    _strip_pdf_from_path,  # noqa: F401
    _walk_page_tree_for_actions,  # noqa: F401
    check_pdf_safety,
)

# ── SVG ───────────────────────────────────────────────────────────────────────
from app.core.file_security._svg import (
    SvgSecurityError,
    _optimize_svg,  # noqa: F401
    check_svg_safety,
    check_svg_safety_stream,
)

# ── ZIP / Gzip ────────────────────────────────────────────────────────────────
from app.core.file_security._zip import (
    _ZIP_MAX_ENTRY_BYTES,  # noqa: F401
    _ZIP_MAX_TOTAL_BYTES,  # noqa: F401
    _gzip_compress_path,  # noqa: F401
    _recompress_zip_path,  # noqa: F401
    _sanitize_zip_entry_name,  # noqa: F401
    get_uncompressed_size,
)

# ── Compress dispatcher ───────────────────────────────────────────────────────
from app.core.file_security.compress import (
    _COMPRESSION_SKIP_THRESHOLD,  # noqa: F401
    CompressResultPath,
    compress_file_path,
)

# ── Strip dispatcher ──────────────────────────────────────────────────────────
from app.core.file_security.strip import strip_metadata_file

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
