import io
import logging
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

MAX_AVATAR_PIXELS = 10_000_000


def process_avatar(
    input_source: Path | bytes | BinaryIO, size: int = 256, quality: int = 60
) -> bytes:
    """Process a bounded, single-frame image into a metadata-free WebP avatar."""
    byte_stream = io.BytesIO(input_source) if isinstance(input_source, bytes) else None
    source = byte_stream if byte_stream is not None else input_source
    try:
        with Image.open(source) as base_img:
            pixels = base_img.width * base_img.height
            if pixels > MAX_AVATAR_PIXELS:
                raise ValueError(f"Avatar exceeds pixel limit ({pixels:,} > {MAX_AVATAR_PIXELS:,})")
            if getattr(base_img, "n_frames", 1) != 1 or getattr(base_img, "is_animated", False):
                raise ValueError("Animated and multi-frame avatars are not supported")

            base_img.load()
            oriented = ImageOps.exif_transpose(base_img)
            try:
                normalized = oriented.convert("RGBA")
                try:
                    fitted = ImageOps.fit(normalized, (size, size), Image.Resampling.LANCZOS)
                    try:
                        with io.BytesIO() as output:
                            fitted.save(output, format="WEBP", quality=quality, method=4)
                            result = output.getvalue()
                    finally:
                        fitted.close()
                finally:
                    normalized.close()
            finally:
                if oriented is not base_img:
                    oriented.close()

            source_name = getattr(input_source, "name", "image_stream")
            if isinstance(source_name, Path):
                source_name = source_name.name
            logger.info("Avatar processed: %s -> %d bytes", source_name, len(result))
            return result

    except Exception as exc:
        logger.error("Failed to process avatar: %s", exc)
        raise ValueError(f"Failed to process avatar: {exc}") from exc
    finally:
        if byte_stream is not None:
            byte_stream.close()
