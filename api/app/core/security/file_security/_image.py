"""Image metadata stripping and compression."""

import io
import logging
from pathlib import Path

from PIL import Image, ImageOps

from app.core.security.processing_paths import make_processing_temp_path as _make_temp_path
from app.core.security.file_security.errors import SanitizationError

logger = logging.getLogger(__name__)

MAX_IMAGE_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

MAX_GIF_FRAMES = 300
MAX_GIF_TOTAL_PIXELS = 100_000_000


def _validate_image_size(img: Image.Image, limit: int = MAX_IMAGE_PIXELS) -> None:
    pixels = img.width * img.height
    if pixels > limit:
        raise SanitizationError(f"Image exceeds pixel limit ({pixels:,} > {limit:,})")


ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


def _validate_image_format_and_frames(img: Image.Image) -> str:
    """Validate that the image is an allowed format and not an unsupported multi-frame/animated image."""
    if img.format is None:
        raise SanitizationError("Unable to determine image format")

    img_format = img.format.upper()
    if img_format not in ALLOWED_IMAGE_FORMATS:
        raise SanitizationError(f"Unsupported image format: '{img_format}'")

    if getattr(img, "n_frames", 1) != 1 or getattr(img, "is_animated", False):
        raise SanitizationError("Animated and multi-frame images are not supported")

    return img_format


def _normalize_clean_image(img: Image.Image) -> Image.Image:
    """Fully decode image, apply EXIF orientation, and construct clean image object preserving alpha."""
    img.load()
    oriented = ImageOps.exif_transpose(img)

    has_alpha = oriented.mode in {"RGBA", "LA", "PA"} or "transparency" in oriented.info

    target_mode = "RGBA" if has_alpha else "RGB"
    try:
        normalized = oriented.convert(target_mode)
        try:
            clean = normalized.copy()
            clean.info.clear()
            return clean
        finally:
            normalized.close()
    finally:
        if oriented is not img:
            oriented.close()


def _save_stripped_image(
    clean: Image.Image, img_format: str, dest: "io.BytesIO | str | Path"
) -> None:
    """Save clean image to dest with metadata stripped using explicit quality parameters."""
    if img_format == "JPEG":
        save_img = clean.convert("RGB") if clean.mode != "RGB" else clean
        try:
            save_img.save(dest, format="JPEG", optimize=True, quality=90, progressive=True)
        finally:
            if save_img is not clean:
                save_img.close()
    elif img_format == "PNG":
        clean.save(dest, format="PNG", optimize=True, compress_level=6)
    elif img_format == "WEBP":
        clean.save(dest, format="WEBP", quality=90, method=6)
    elif img_format == "GIF":
        clean.save(dest, format="GIF")
    else:
        clean.save(dest, format=img_format)


def _strip_image_metadata(file_bytes: bytes) -> bytes:
    """Remove EXIF data, comments, and auxiliary chunks from images by re-saving clean pixel data."""
    try:
        with io.BytesIO(file_bytes) as source, Image.open(source) as img:
            _validate_image_size(img)
            img_format = _validate_image_format_and_frames(img)
            clean = _normalize_clean_image(img)
            try:
                with io.BytesIO() as output:
                    _save_stripped_image(clean, img_format, output)
                    return output.getvalue()
            finally:
                clean.close()
    except SanitizationError:
        raise
    except Exception as exc:
        logger.warning("Image metadata strip failed: %s", exc)
        raise SanitizationError("Failed to sanitize image metadata") from exc


def _strip_image_from_path(file_path: Path) -> Path:
    """Remove EXIF data from images by re-saving clean pixel data from a file path after EXIF orientation."""
    new_path = None
    try:
        with Image.open(file_path) as img:
            _validate_image_size(img)
            img_format = _validate_image_format_and_frames(img)
            clean = _normalize_clean_image(img)
            try:
                new_path = _make_temp_path()
                _save_stripped_image(clean, img_format, str(new_path))
                return new_path
            finally:
                clean.close()
    except SanitizationError:
        if new_path is not None:
            new_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.warning("Image metadata strip path failed: %s", exc)
        if new_path is not None:
            new_path.unlink(missing_ok=True)
        raise SanitizationError("Failed to sanitize image metadata") from exc


def _save_compressed_image(img: Image.Image, img_format: str, dest: "io.BytesIO | Path") -> None:
    """Save img to dest with high compression settings."""
    if img_format == "JPEG":
        save_img = img.convert("RGB") if img.mode != "RGB" else img
        try:
            save_img.save(dest, format="JPEG", optimize=True, quality=75, progressive=True)
        finally:
            if save_img is not img:
                save_img.close()
    elif img_format == "PNG":
        img.save(dest, format="PNG", optimize=True, compress_level=9)
    elif img_format == "WEBP":
        img.save(dest, format="WEBP", quality=75, method=6)
    else:
        img.save(dest, format=img_format)


FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


def _compress_image_path(file_path: Path) -> tuple[Path, str]:
    """Resize image to max 2048px (2K) and compress deeply (Quality 75), returning (path, mime_type)."""
    out_name = None
    try:
        with Image.open(file_path) as img:
            _validate_image_size(img)
            actual_format = _validate_image_format_and_frames(img)
            original_mime = FORMAT_TO_MIME[actual_format]

            img.load()
            oriented = ImageOps.exif_transpose(img)
            try:
                max_size = 2048
                if oriented.width > max_size or oriented.height > max_size:
                    oriented.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

                # Compress raster images to WebP.
                target_format = "WEBP"
                target_mime = "image/webp"
                out_name = _make_temp_path()
                _save_compressed_image(oriented, target_format, out_name)
                if out_name.stat().st_size < file_path.stat().st_size:
                    return out_name, target_mime

                out_name.unlink(missing_ok=True)
                return file_path, original_mime
            finally:
                if oriented is not img:
                    oriented.close()
    except SanitizationError:
        if out_name is not None:
            out_name.unlink(missing_ok=True)
        raise
    except Exception as exc:
        logger.warning("Image compression failed for %s: %s", file_path, exc)
        if out_name is not None:
            out_name.unlink(missing_ok=True)
        # Compression is best effort, but the fallback MIME must describe the
        # original bytes rather than inventing a type.
        try:
            with Image.open(file_path) as img:
                if img.format and img.format.upper() in FORMAT_TO_MIME:
                    return file_path, FORMAT_TO_MIME[img.format.upper()]
        except Exception:
            pass
    return file_path, "application/octet-stream"
