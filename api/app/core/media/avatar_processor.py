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
    """Process an image into a secure and compressed square WebP avatar.

    1. Opens the image (from file path, bytes, or binary stream).
    2. Enforces MAX_AVATAR_PIXELS header check to prevent decompression bombs.
    3. Autorotates based on EXIF metadata.
    4. Crops and resizes to a square of `size` x `size` pixels using LANCZOS.
    5. Converts to RGBA format.
    6. Saves as compressed WebP in an in-memory BytesIO buffer, stripping all EXIF/metadata.

    Returns:
        bytes: The WebP encoded image bytes.
    """
    try:
        source = (
            io.BytesIO(input_source) if isinstance(input_source, bytes) else input_source
        )
        with Image.open(source) as base_img:
            pixels = base_img.width * base_img.height
            if pixels > MAX_AVATAR_PIXELS:
                raise ValueError(
                    f"Avatar exceeds pixel limit ({pixels:,} > {MAX_AVATAR_PIXELS:,})"
                )
            # Handle EXIF orientation
            img = ImageOps.exif_transpose(base_img)

            # Convert to RGBA
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")

            # Crop to square
            img = ImageOps.fit(img, (size, size), Image.Resampling.LANCZOS)

            # Save as WebP into in-memory buffer
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=quality, method=4)
            result = buf.getvalue()

            source_name = getattr(input_source, "name", "image_stream")
            if isinstance(source_name, Path):
                source_name = source_name.name
            logger.info("Avatar processed: %s -> %d bytes", source_name, len(result))
            return result

    except Exception as exc:
        logger.error("Failed to process avatar: %s", exc)
        raise ValueError(f"Failed to process avatar: {exc}") from exc
