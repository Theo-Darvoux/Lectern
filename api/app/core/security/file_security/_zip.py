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
import unicodedata
import zipfile
from pathlib import Path

from PIL import Image, ImageOps

from app.core.security.processing_paths import make_processing_temp_path as _make_temp_path
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
_ZIP_MAX_COMPRESSION_RATIO = 1_000
_ZIP_RATIO_MIN_UNCOMPRESSED_BYTES = 1 * 1024 * 1024
_SANITIZED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

# Raster image extensions we compress in-place inside ZIP-based formats (OOXML, EPUB, ODF).
# SVG is intentionally excluded: it requires a dedicated security check (_svg.py).
# Vector formats (EMF, WMF) are excluded: Pillow cannot reliably round-trip them.
# Format is preserved (no WebP conversion) so OOXML relationship XML keeps its references valid.
_ZIP_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".tiff", ".tif", ".webp", ".bmp"}
)

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
    """Canonicalize a ZIP member name without permitting traversal semantics."""

    is_dir = name.endswith(("/", "\\"))
    normalized = unicodedata.normalize("NFKC", name)
    normalized = re.sub(r"[\x00-\x1f\x7f]", "", normalized)
    normalized = re.sub(r"^([a-zA-Z]:[\\/]|[/\\]+)", "", normalized)
    parts = re.split(r"[\\/]", normalized)
    safe_parts = ["_" if part in {".", ".."} else part[:255] for part in parts if part]
    safe_name = "/".join(safe_parts) or "_unknown_"
    return f"{safe_name}/" if is_dir else safe_name


def _canonical_zip_name(name: str) -> str:
    return unicodedata.normalize("NFC", name.rstrip("/")).casefold()


def _register_zip_name(registry: dict[str, bool], safe_name: str, *, is_dir: bool) -> None:
    """Reject duplicates and file/directory hierarchy conflicts after canonicalization."""

    canonical = _canonical_zip_name(safe_name)
    if canonical in registry:
        raise ValueError(f"ZIP contains duplicate sanitized entry '{safe_name}'")

    parts = canonical.split("/")
    for index in range(1, len(parts)):
        parent = "/".join(parts[:index])
        if registry.get(parent) is False:
            raise ValueError(f"ZIP entry '{safe_name}' is nested beneath file '{parent}'")

    if not is_dir:
        prefix = f"{canonical}/"
        if any(existing.startswith(prefix) for existing in registry):
            raise ValueError(f"ZIP file '{safe_name}' conflicts with an existing directory")

    registry[canonical] = is_dir


def _validate_zip_info(item: zipfile.ZipInfo) -> None:
    if item.flag_bits & 0x1:
        raise ValueError(f"Encrypted ZIP entry '{item.filename}' is not supported")
    if item.file_size > _ZIP_MAX_ENTRY_BYTES:
        raise ValueError(f"ZIP entry '{item.filename}' is too large")
    if (
        item.file_size >= _ZIP_RATIO_MIN_UNCOMPRESSED_BYTES
        and item.compress_size > 0
        and item.file_size / item.compress_size > _ZIP_MAX_COMPRESSION_RATIO
    ):
        raise ValueError(f"ZIP entry '{item.filename}' has a suspicious compression ratio")


def _sanitized_zip_info(name: str, *, compress_type: int, is_dir: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=_SANITIZED_ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED if is_dir else compress_type
    info.external_attr = ((0o40700 if is_dir else 0o100600) << 16) | (0x10 if is_dir else 0)
    return info


def _has_trivial_alpha(img: Image.Image, threshold: float = 0.95) -> bool:
    """Check whether an RGBA image's alpha channel is mostly fully-opaque.

    Uses Pillow's C-level histogram (fast) instead of per-pixel Python iteration.
    Returns True if >= *threshold* fraction of pixels have alpha > 250.
    """
    alpha = img.getchannel("A")
    try:
        hist = alpha.histogram()  # 256 buckets
        opaque_pixels = sum(hist[251:])
        total_pixels = img.width * img.height
        return opaque_pixels >= total_pixels * threshold
    finally:
        alpha.close()


def _flatten_rgba(img: Image.Image) -> Image.Image:
    """Composite an RGBA image onto a white background, returning an RGB image."""
    background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    alpha = img.getchannel("A")
    try:
        background.paste(img, mask=alpha)
        return background.convert("RGB")
    finally:
        alpha.close()
        background.close()


def _compress_animated_gif(img: Image.Image, data: bytes) -> tuple[bytes, bool]:
    """Bound, resize, and re-encode an animated GIF without carrying metadata."""

    n_frames = int(getattr(img, "n_frames", 1))
    if n_frames > MAX_GIF_FRAMES:
        raise ValueError(f"Animated image exceeds frame limit ({MAX_GIF_FRAMES})")

    needs_resize = img.width > _GIF_MAX_DIM or img.height > _GIF_MAX_DIM
    step = max(1, math.ceil(n_frames / _GIF_MAX_FRAMES))
    frames: list[Image.Image] = []
    durations: list[int] = []
    total_pixels = 0
    try:
        for index in range(n_frames):
            img.seek(index)
            _validate_image_size(img)
            total_pixels += img.width * img.height
            if total_pixels > MAX_GIF_TOTAL_PIXELS:
                raise ValueError("Animated image exceeds cumulative pixel budget")
            if index % step:
                continue
            frame = img.convert("RGBA")
            if needs_resize:
                frame.thumbnail((_GIF_MAX_DIM, _GIF_MAX_DIM), Image.Resampling.LANCZOS)
            frames.append(frame)
            durations.append(int(img.info.get("duration", 100)) * step)

        if not frames:
            raise ValueError("Animated GIF contains no decodable frames")

        with io.BytesIO() as output:
            frames[0].save(
                output,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=int(img.info.get("loop", 0)),
                optimize=True,
                comment=b"",
            )
            result = output.getvalue()
        return result, result != data
    finally:
        for frame in frames:
            frame.close()


def _sanitize_embedded_image(
    data: bytes,
    entry_name: str,
    *,
    expected_format: str | None = None,
) -> bytes:
    """Remove metadata from one embedded raster image while preserving its format."""

    expected_formats = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".gif": "GIF",
        ".tif": "TIFF",
        ".tiff": "TIFF",
        ".webp": "WEBP",
        ".bmp": "BMP",
    }
    extension = Path(entry_name).suffix.casefold()
    expected = expected_format or expected_formats.get(extension)
    if expected is None:
        return data
    expected = expected.upper()

    try:
        with io.BytesIO(data) as source, Image.open(source) as img:
            _validate_image_size(img)
            actual = (img.format or "").upper()
            if actual != expected:
                raise ValueError(
                    f"Embedded image '{entry_name}' format mismatch: expected {expected}, got {actual or 'unknown'}"
                )
            if getattr(img, "n_frames", 1) > 1 or getattr(img, "is_animated", False):
                if actual != "GIF":
                    raise ValueError(f"Embedded multi-frame image '{entry_name}' is not supported")
                return _compress_animated_gif(img, data)[0]

            img.load()
            oriented = ImageOps.exif_transpose(img)
            try:
                with io.BytesIO() as output:
                    if actual == "JPEG":
                        converted = oriented.convert("RGB")
                        try:
                            converted.save(output, format="JPEG", quality=95, optimize=True)
                        finally:
                            converted.close()
                    elif actual == "PNG":
                        oriented.save(output, format="PNG", optimize=True, compress_level=6)
                    elif actual == "GIF":
                        oriented.save(output, format="GIF", optimize=True, comment=b"")
                    elif actual == "WEBP":
                        oriented.save(output, format="WEBP", lossless=True, method=6)
                    elif actual == "TIFF":
                        oriented.save(output, format="TIFF", compression="tiff_deflate")
                    elif actual == "BMP":
                        oriented.save(output, format="BMP")
                    else:
                        raise ValueError(f"Unsupported embedded image format: {actual}")
                    return output.getvalue()
            finally:
                if oriented is not img:
                    oriented.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to sanitize embedded image '{entry_name}'") from exc


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
        with io.BytesIO(data) as source, Image.open(source) as img, io.BytesIO() as buf:
            _validate_image_size(img)
            img_format = img.format or "JPEG"

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
                    try:
                        quantized = flat.quantize(colors=256, method=2)
                        try:
                            quantized.save(
                                buf, format="PNG", optimize=True, compress_level=6
                            )
                        finally:
                            quantized.close()
                    finally:
                        flat.close()
                elif has_alpha:
                    # Real transparency: just resize + optimize (quantize loses alpha)
                    img.save(buf, format="PNG", optimize=True, compress_level=6)
                else:
                    # Opaque PNG: quantize for big savings
                    rgb = img.convert("RGB") if img.mode != "RGB" else img
                    try:
                        quantized = rgb.quantize(colors=256, method=2)
                        try:
                            quantized.save(
                                buf, format="PNG", optimize=True, compress_level=6
                            )
                        finally:
                            quantized.close()
                    finally:
                        if rgb is not img:
                            rgb.close()

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
    """Recompress a ZIP while enforcing names, sizes, encryption, and image budgets."""

    out_name = str(_make_temp_path(suffix=".zip"))
    try:
        must_use_output = False
        with (
            zipfile.ZipFile(file_path, "r") as zin,
            zipfile.ZipFile(out_name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout,
        ):
            entries = zin.infolist()
            if len(entries) > _ZIP_MAX_ENTRIES:
                raise ValueError(f"ZIP archive contains too many entries (max {_ZIP_MAX_ENTRIES})")
            if sum(item.file_size for item in entries) > _ZIP_MAX_TOTAL_BYTES:
                raise ValueError("ZIP archive uncompressed content is too large")

            total_actual = 0
            registered_names: dict[str, bool] = {}
            for item in entries:
                _validate_zip_info(item)
                safe_name = _sanitize_zip_entry_name(item.filename)
                is_dir = item.is_dir() or safe_name.endswith("/")
                if is_dir and not safe_name.endswith("/"):
                    safe_name = f"{safe_name}/"
                _register_zip_name(registered_names, safe_name, is_dir=is_dir)
                must_use_output |= safe_name != item.filename

                if is_dir:
                    zout.writestr(
                        _sanitized_zip_info(
                            safe_name,
                            compress_type=zipfile.ZIP_STORED,
                            is_dir=True,
                        ),
                        b"",
                    )
                    continue

                entry_ext = Path(safe_name).suffix.casefold()
                compress_type = (
                    zipfile.ZIP_STORED
                    if safe_name == "mimetype" or entry_ext in _INCOMPRESSIBLE_EXTENSIONS
                    else zipfile.ZIP_DEFLATED
                )
                sanitized_info = _sanitized_zip_info(
                    safe_name,
                    compress_type=compress_type,
                )

                if entry_ext in _ZIP_IMAGE_EXTENSIONS:
                    entry_data = _read_zip_entry_bounded(zin, item, total_actual)
                    total_actual += len(entry_data)
                    sanitized_data = _sanitize_embedded_image(entry_data, safe_name)
                    must_use_output |= sanitized_data != entry_data
                    if len(entry_data) >= _ZIP_IMAGE_MIN_BYTES:
                        sanitized_data, sanitized_info.compress_type = _compress_zip_image_entry(
                            sanitized_data,
                            safe_name,
                        )
                    else:
                        sanitized_info.compress_type = zipfile.ZIP_STORED
                    zout.writestr(sanitized_info, sanitized_data)
                    continue

                written = 0
                with zin.open(item) as src, zout.open(sanitized_info, "w") as dest:
                    while chunk := src.read(_CHUNK_SIZE):
                        written += len(chunk)
                        total_actual += len(chunk)
                        if written > _ZIP_MAX_ENTRY_BYTES:
                            raise ValueError(f"ZIP entry '{item.filename}' expanded beyond limit")
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
    out_name = str(_make_temp_path(suffix=".gz"))
    try:
        import shutil

        with open(file_path, "rb") as f_in, open(out_name, "wb") as raw_out:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw_out,
                mtime=0,
            ) as f_out:
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
