"""Image metadata stripping and compression.

Provides:
- GIF DoS guards (frame count + total pixel budget)
- _save_stripped_image / _save_compressed_image: format-dispatch image savers
- _strip_image_from_path: EXIF removal via PIL re-save
- _compress_image_path: resize to 2K + aggressive quality reduction
"""

import io
import logging
import tempfile
from pathlib import Path

from PIL import Image

from app.core.file_security._concurrency import (
    _get_concurrency_guard,  # noqa: F401 (re-exported for strip.py)
)

logger = logging.getLogger(__name__)

# Decompression-bomb protection: reject images with more than 50M pixels.
Image.MAX_IMAGE_PIXELS = 50_000_000

# GIF DoS protection: limit the number of frames to process
MAX_GIF_FRAMES = 500
# GIF DoS protection: cumulative pixel budget across all frames.
# 100M pixels ≈ ~400 MB of RGBA data in memory at peak.
MAX_GIF_TOTAL_PIXELS = 100_000_000


def _strip_gif_to_dest(img: Image.Image, dest: "io.BytesIO | str") -> None:
    """Extract GIF frames with DoS guards and re-save stripped to *dest*."""
    frames: list[Image.Image] = []
    total_pixels = 0
    try:
        while True:
            total_pixels += img.size[0] * img.size[1]
            if total_pixels > MAX_GIF_TOTAL_PIXELS:
                raise ValueError(
                    f"Animated GIF exceeds memory budget "
                    f"({total_pixels:,} pixels > {MAX_GIF_TOTAL_PIXELS:,} limit)"
                )
            frames.append(img.copy())
            if len(frames) > MAX_GIF_FRAMES:
                logger.warning("GIF exceeded frame limit (%d)", MAX_GIF_FRAMES)
                break
            img.seek(img.tell() + 1)
    except EOFError:
        pass

    if len(frames) > 1:
        durations = []
        for i, _frame in enumerate(frames):
            img.seek(i)
            durations.append(img.info.get("duration", 100))
        loop = img.info.get("loop", 0)
        frames[0].save(
            dest,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=loop,
        )
    else:
        frames[0].save(dest, format="GIF")


def _save_stripped_image(img: Image.Image, img_format: str, dest: "io.BytesIO | str") -> None:
    """Save *img* to *dest* with metadata stripped (format-dispatch helper)."""
    if img_format == "GIF":
        _strip_gif_to_dest(img, dest)
    elif img_format == "JPEG":
        img.save(dest, format="JPEG", optimize=True, progressive=True)
    elif img_format == "PNG":
        img.save(dest, format="PNG", optimize=True, compress_level=6)
    elif img_format == "WEBP":
        img.save(dest, format="WEBP", method=6)
    else:
        img.save(dest, format=img_format)


def _strip_image_metadata(file_bytes: bytes) -> bytes:
    """Remove EXIF data from images by re-saving them (bytes → bytes)."""
    try:
        with Image.open(io.BytesIO(file_bytes)) as img:
            output = io.BytesIO()
            _save_stripped_image(img, img.format or "JPEG", output)
            return output.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("Image metadata strip failed: %s", exc)
        return file_bytes


def _strip_image_from_path(file_path: Path) -> Path:
    """Remove EXIF data from images by re-saving them from a file path."""
    new_path = None
    try:
        with Image.open(file_path) as img:
            new_path = Path(tempfile.NamedTemporaryFile(delete=False).name)
            _save_stripped_image(img, img.format or "JPEG", str(new_path))
            return new_path
    except ValueError:
        if new_path is not None:
            new_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.warning("Image metadata strip path failed: %s", exc)
        if new_path is not None:
            new_path.unlink(missing_ok=True)
        return file_path


def _save_compressed_image(img: Image.Image, img_format: str, dest: "io.BytesIO | Path") -> None:
    """Save *img* to *dest* with aggressive quality/compression settings."""
    if img_format == "JPEG":
        img.save(dest, format="JPEG", optimize=True, quality=75, progressive=True)
    elif img_format == "PNG":
        if img.mode in ("RGBA", "LA"):
            img.save(dest, format="PNG", optimize=True, compress_level=9)
        else:
            # Lossy quantization for PNG (similar to pngquant)
            img.quantize(colors=256).save(dest, format="PNG", optimize=True)
    elif img_format == "WEBP":
        img.save(dest, format="WEBP", quality=75, method=6)
    else:
        img.save(dest, format=img_format)


def _compress_image_path(file_path: Path) -> Path:
    """Resize image to max 2048px (2K) and compress deeply (Quality 75).
    Forces WEBP conversion for all non-animated images.
    """
    out_name = None
    try:
        with Image.open(file_path) as img:
            max_size = 2048
            if img.width > max_size or img.height > max_size:
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            img_format = img.format or "JPEG"
            if img_format != "GIF":
                img_format = "WEBP"

            out_name = Path(tempfile.NamedTemporaryFile(delete=False).name)
            _save_compressed_image(img, img_format, out_name)
            if out_name.stat().st_size < file_path.stat().st_size:
                return out_name
            out_name.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Image compression failed for %s: %s", file_path, exc)
        if out_name is not None:
            out_name.unlink(missing_ok=True)
    return file_path
