"""ZIP and gzip utilities used across the file security pipeline.

Provides:
- _ZIP_MAX_ENTRY_BYTES / _ZIP_MAX_TOTAL_BYTES: ZIP-bomb thresholds
- _sanitize_zip_entry_name: path traversal sanitizer for ZIP entry names
- _recompress_zip_path: ZIP re-deflate with image compression + bomb + traversal protection
- _gzip_compress_path: gzip level 9 compression of a file to a new temp file
- get_uncompressed_size: safe read of ZIP central directory for disk-space guards
"""

import gzip
import io
import logging
import math
import re
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from app.core.security.file_security._image import (
    MAX_GIF_FRAMES,
    MAX_GIF_TOTAL_PIXELS,
    _validate_image_size,
)

logger = logging.getLogger(__name__)

# ZIP bomb protection thresholds
_ZIP_MAX_ENTRY_BYTES = 200 * 1024 * 1024  # 200 MB per entry
_ZIP_MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500 MB total uncompressed
_ZIP_MAX_ENTRIES = 10_000

# Raster image extensions we compress in-place inside ZIP-based formats (OOXML, EPUB, ODF).
# SVG is intentionally excluded: it requires a dedicated security check (_svg.py).
# Vector formats (EMF, WMF) are excluded: Pillow cannot reliably round-trip them.
# Format is preserved (no WebP conversion) so OOXML relationship XML keeps its references valid.
_ZIP_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".tiff", ".tif"})

# Skip image compression for tiny entries — icons and 1x1 spacers.
_ZIP_IMAGE_MIN_BYTES = 10 * 1024  # 10 KiB

# Extensions that are already compressed — DEFLATE on top adds CPU cost with no benefit.
_INCOMPRESSIBLE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".tiff",
        ".tif",
        ".webp",
        ".mp3",
        ".mp4",
        ".m4a",
        ".m4v",
        ".webm",
        ".ogg",
        ".opus",
        ".flac",
        ".aac",
        ".avi",
        ".mov",
        ".wmv",
        ".wma",
        ".zip",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".zst",
    }
)

# I/O buffer for streaming zip entries (256 KiB)
_CHUNK_SIZE = 256 * 1024

# Maximum animated GIF frames — beyond this we subsample to cut size.
_GIF_MAX_FRAMES = 60

# Maximum dimension for animated GIFs inside documents.
_GIF_MAX_DIM = 480


def _sanitize_zip_entry_name(name: str) -> str:
    """Sanitize a ZIP entry filename to prevent path traversal on extraction.

    - Strips leading slashes and drive specifiers
    - Replaces ``..`` path components with ``_``
    - Removes null bytes and ASCII control characters
    - Truncates each path segment to 255 characters
    """
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"^([a-zA-Z]:[\\/]|[/\\]+)", "", name)
    parts = re.split(r"[\\/]", name)
    safe_parts = ["_" if (p == ".." or p == ".") else p[:255] for p in parts if p]
    return "/".join(safe_parts) or "_unknown_"


def _has_trivial_alpha(img: Image.Image, threshold: float = 0.95) -> bool:
    """Check whether an RGBA image's alpha channel is mostly fully-opaque.

    Uses Pillow's C-level histogram (fast) instead of per-pixel Python iteration.
    Returns True if >= *threshold* fraction of pixels have alpha > 250.
    """
    alpha = img.split()[-1]
    hist = alpha.histogram()  # 256 buckets
    opaque_pixels = sum(hist[251:])
    total_pixels = img.width * img.height
    return opaque_pixels >= total_pixels * threshold


def _flatten_rgba(img: Image.Image) -> Image.Image:
    """Composite an RGBA image onto a white background, returning an RGB image."""
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(img, mask=img.split()[3])
    return bg.convert("RGB")


def _compress_animated_gif(img: Image.Image, data: bytes) -> tuple[bytes, bool]:
    """Compress an animated GIF by resizing frames and subsampling if needed.

    Returns (compressed_bytes, was_compressed).
    """
    n_frames = getattr(img, "n_frames", 1)
    if n_frames > MAX_GIF_FRAMES:
        raise ValueError(f"Animated image exceeds frame limit ({MAX_GIF_FRAMES})")
    _validate_image_size(img)
    needs_resize = img.width > _GIF_MAX_DIM or img.height > _GIF_MAX_DIM
    needs_subsample = n_frames > _GIF_MAX_FRAMES

    if not needs_resize and not needs_subsample:
        # Re-save with optimize to strip metadata at minimum
        buf = io.BytesIO()
        img.save(buf, format="GIF", save_all=True, optimize=True)
        result = buf.getvalue()
        return (result, True) if len(result) < len(data) else (data, False)

    # Extract frames, optionally subsampling
    step = max(1, math.ceil(n_frames / _GIF_MAX_FRAMES))
    frames: list[Image.Image] = []
    durations: list[int] = []
    total_pixels = 0

    for i in list(range(0, n_frames, step))[:_GIF_MAX_FRAMES]:
        img.seek(i)
        _validate_image_size(img)
        total_pixels += img.width * img.height
        if total_pixels > MAX_GIF_TOTAL_PIXELS:
            raise ValueError("Animated image exceeds cumulative pixel budget")
        frame = img.copy()
        if needs_resize:
            frame.thumbnail((_GIF_MAX_DIM, _GIF_MAX_DIM), Image.Resampling.LANCZOS)
        frames.append(frame)
        dur = img.info.get("duration", 100)
        durations.append(dur * step)  # multiply duration to maintain perceived speed

    if not frames:
        return data, False

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=img.info.get("loop", 0),
        optimize=True,
    )
    result = buf.getvalue()
    return (result, True) if len(result) < len(data) else (data, False)


def _compress_zip_image_entry(data: bytes, entry_name: str) -> tuple[bytes, int]:
    """Aggressively compress a raster image entry from a ZIP archive.

    Strategy per format:
    - **JPEG**: quality=45, max 1600px, progressive.
    - **PNG (trivial alpha)**: flatten to RGB on white → quantize to 256 colours → PNG.
      Quantization gives 5-10x reduction on screenshot-type images which dominate
      embedded document content.
    - **PNG (real alpha)**: resize to max 1600px, re-save with optimize + compress_level=6.
    - **PNG (opaque)**: quantize to 256 colours → PNG.
    - **GIF (animated)**: resize to max 480px, subsample frames if > 60.
    - **GIF (static) / TIFF / other**: re-save to strip metadata.

    Format is always preserved — OOXML relationship XML references files by name.

    Returns (bytes, compress_type). All images return ZIP_STORED since image formats
    are already compressed; layering DEFLATE on top wastes CPU for zero gain.

    Any Pillow failure is swallowed (fail-open): returns original data + ZIP_STORED.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            _validate_image_size(img)
            img_format = img.format or "JPEG"
            buf = io.BytesIO()

            if img_format == "JPEG":
                max_dim = 1600
                if img.width > max_dim or img.height > max_dim:
                    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                img.save(buf, format="JPEG", optimize=True, quality=45, progressive=True)
                compressed = buf.getvalue()
                if len(compressed) < len(data):
                    return compressed, zipfile.ZIP_STORED

            elif img_format == "PNG":
                max_dim = 1600
                if img.width > max_dim or img.height > max_dim:
                    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

                has_alpha = img.mode in ("RGBA", "LA", "PA")

                if has_alpha and _has_trivial_alpha(img):
                    # Mostly-opaque RGBA: flatten and quantize for massive savings
                    flat = _flatten_rgba(img)
                    quantized = flat.quantize(colors=256, method=2)
                    quantized.save(buf, format="PNG", optimize=True, compress_level=6)
                elif has_alpha:
                    # Real transparency: just resize + optimize (quantize loses alpha)
                    img.save(buf, format="PNG", optimize=True, compress_level=6)
                else:
                    # Opaque PNG: quantize for big savings
                    rgb = img.convert("RGB") if img.mode != "RGB" else img
                    quantized = rgb.quantize(colors=256, method=2)
                    quantized.save(buf, format="PNG", optimize=True, compress_level=6)

                compressed = buf.getvalue()
                if len(compressed) < len(data):
                    return compressed, zipfile.ZIP_STORED

            elif img_format == "GIF":
                if getattr(img, "is_animated", False):
                    compressed, was_compressed = _compress_animated_gif(img, data)
                    if was_compressed:
                        return compressed, zipfile.ZIP_STORED
                else:
                    img.save(buf, format="GIF", optimize=True)
                    compressed = buf.getvalue()
                    if len(compressed) < len(data):
                        return compressed, zipfile.ZIP_STORED
            else:
                # TIFF and anything else: re-save to strip metadata
                img.save(buf, format=img_format)
                compressed = buf.getvalue()
                if len(compressed) < len(data):
                    return compressed, zipfile.ZIP_STORED
    except ValueError:
        # Pixel/frame limits are security boundaries, not optional compression
        # failures. Propagate them so the containing archive is rejected.
        raise
    except Exception as exc:
        logger.debug("Image compression inside ZIP skipped for %r: %s", entry_name, exc)
    return data, zipfile.ZIP_STORED


def _recompress_zip_path(file_path: Path) -> Path:
    """Re-compress a ZIP file with aggressive image optimisation + bomb/traversal protection.

    Entries are copied one at a time so the archive's uncompressed contents are
    never retained in memory. Raster images are the only entries materialized,
    and only one image is materialized at a time for Pillow processing.

    Compression strategy per entry type:
    - Raster images >= 10 KiB: aggressive Pillow compression (quantize PNGs,
      quality=45 JPEGs, resize+subsample animated GIFs). All stored as ZIP_STORED.
    - Already-compressed extensions (.jpg, .mp3, etc.): ZIP_STORED.
    - Compressible entries (XML, CSS, etc.): ZIP_DEFLATED at compresslevel=6.

    Returns a new temp file path if the output is smaller or any entry name had
    to be sanitized; otherwise returns the original path unchanged.

    Raises:
        ValueError: if entry or total size exceeds configured limits.
        zipfile.BadZipFile: if the input is not a valid ZIP.
    """
    out_name = tempfile.NamedTemporaryFile(suffix=".zip", delete=False).name
    try:
        must_use_output = False
        with (
            zipfile.ZipFile(file_path, "r") as zin,
            zipfile.ZipFile(out_name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout,
        ):
            entries = zin.infolist()
            if len(entries) > _ZIP_MAX_ENTRIES:
                raise ValueError(f"ZIP archive contains too many entries (max {_ZIP_MAX_ENTRIES})")
            total_declared = sum(i.file_size for i in entries)
            if total_declared > _ZIP_MAX_TOTAL_BYTES:
                raise ValueError("ZIP archive uncompressed content is too large")

            total_actual = 0
            sanitized_names: set[str] = set()
            for item in entries:
                if item.file_size > _ZIP_MAX_ENTRY_BYTES:
                    raise ValueError(f"ZIP entry '{item.filename}' is too large")

                safe_name = _sanitize_zip_entry_name(item.filename)
                if safe_name in sanitized_names:
                    raise ValueError(f"ZIP contains duplicate sanitized entry '{safe_name}'")
                sanitized_names.add(safe_name)
                must_use_output |= safe_name != item.filename

                entry_ext = Path(safe_name).suffix.lower()
                is_image = (
                    entry_ext in _ZIP_IMAGE_EXTENSIONS
                    and item.file_size >= _ZIP_IMAGE_MIN_BYTES
                    and not item.is_dir()
                )

                sanitized_info = zipfile.ZipInfo(safe_name, date_time=item.date_time)
                # EPUB requires its `mimetype` entry to remain uncompressed. Other
                # already-compressed formats likewise gain nothing from DEFLATE.
                sanitized_info.compress_type = (
                    zipfile.ZIP_STORED
                    if safe_name == "mimetype" or entry_ext in _INCOMPRESSIBLE_EXTENSIONS
                    else zipfile.ZIP_DEFLATED
                )

                if is_image:
                    entry_data = _read_zip_entry_bounded(zin, item, total_actual)
                    total_actual += len(entry_data)
                    comp_data, comp_type = _compress_zip_image_entry(entry_data, safe_name)
                    sanitized_info.compress_type = comp_type
                    zout.writestr(sanitized_info, comp_data)
                    continue

                written = 0
                with zin.open(item) as src, zout.open(sanitized_info, "w") as dest:
                    while chunk := src.read(_CHUNK_SIZE):
                        written += len(chunk)
                        if written > _ZIP_MAX_ENTRY_BYTES:
                            raise ValueError(f"ZIP entry '{item.filename}' expanded beyond limit")
                        total_actual += len(chunk)
                        if total_actual > _ZIP_MAX_TOTAL_BYTES:
                            raise ValueError(
                                "ZIP archive actual uncompressed content exceeds total limit"
                            )
                        dest.write(chunk)

        if must_use_output or Path(out_name).stat().st_size < file_path.stat().st_size:
            return Path(out_name)
    except Exception:
        Path(out_name).unlink(missing_ok=True)
        raise
    Path(out_name).unlink(missing_ok=True)
    return file_path


def _read_zip_entry_bounded(
    archive: zipfile.ZipFile,
    item: zipfile.ZipInfo,
    total_before: int,
) -> bytes:
    """Read one ZIP entry while enforcing per-entry and archive size limits."""
    chunks: list[bytes] = []
    written = 0
    with archive.open(item) as src:
        while chunk := src.read(_CHUNK_SIZE):
            written += len(chunk)
            if written > _ZIP_MAX_ENTRY_BYTES:
                raise ValueError(f"ZIP entry '{item.filename}' expanded beyond limit")
            if total_before + written > _ZIP_MAX_TOTAL_BYTES:
                raise ValueError("ZIP archive actual uncompressed content exceeds total limit")
            chunks.append(chunk)
    return b"".join(chunks)


def _gzip_compress_path(file_path: Path) -> Path:
    """Compress *file_path* with gzip level 9, returning a new temp file.

    Returns the original path if the compressed output is not smaller.
    """
    out_name = tempfile.NamedTemporaryFile(suffix=".gz", delete=False).name
    try:
        with open(file_path, "rb") as f_in, gzip.open(out_name, "wb", compresslevel=9) as f_out:
            import shutil

            shutil.copyfileobj(f_in, f_out)
        if Path(out_name).stat().st_size < file_path.stat().st_size:
            return Path(out_name)
    except Exception:
        Path(out_name).unlink(missing_ok=True)
        raise
    Path(out_name).unlink(missing_ok=True)
    return file_path


def get_uncompressed_size(file_path: Path) -> int:
    """Return total uncompressed size of all entries in a ZIP archive.

    Safe to call: only reads the central directory (no full-file read or extraction).
    Returns 0 if the file is not a valid ZIP. Security-limit violations are
    propagated so callers cannot mistake a rejected archive for an empty one.
    """
    try:
        if not zipfile.is_zipfile(file_path):
            return 0
        with zipfile.ZipFile(file_path, "r") as z:
            entries = z.infolist()
            if len(entries) > _ZIP_MAX_ENTRIES:
                raise ValueError(f"ZIP archive contains too many entries (max {_ZIP_MAX_ENTRIES})")
            return sum(info.file_size for info in entries)
    except (OSError, zipfile.BadZipFile):
        return 0
