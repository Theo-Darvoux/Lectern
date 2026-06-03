import logging
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Guard against decompression bombs
Image.MAX_IMAGE_PIXELS = 10_000_000  # 10MP is plenty for an avatar


def process_avatar(input_path: Path, size: int = 256, quality: int = 60) -> Path:
    """
    Process an image into a secure, heavily compressed square avatar.

    1. Opens the image.
    2. Autorotates based on EXIF.
    3. Crops and resizes to a square of `size` x `size`.
    4. Converts to RGB (white background for transparency) or keeps RGBA for WebP.
       Actually, we'll force WebP for everything.
    5. Saves as WebP with the specified quality.
    6. Strips all metadata (implied by re-saving).
    """
    try:
        with Image.open(input_path) as base_img:
            # Handle EXIF orientation
            img = ImageOps.exif_transpose(base_img)

            # Convert to RGBA if not already (to handle transparency correctly)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")

            # If it's a static image but has transparency, we'll keep it for WebP.
            # Crop to square
            img = ImageOps.fit(img, (size, size), Image.Resampling.LANCZOS)

            # Create a temporary file for the output
            out_file = tempfile.NamedTemporaryFile(delete=False, suffix=".webp")
            out_path = Path(out_file.name)
            out_file.close()

            # Save as WebP
            img.save(out_path, format="WEBP", quality=quality, method=6)

            logger.info(
                "Avatar processed: %s -> %s (%d bytes)",
                input_path.name,
                out_path.name,
                out_path.stat().st_size,
            )
            return out_path

    except Exception as exc:
        logger.error("Failed to process avatar %s: %s", input_path, exc)
        raise ValueError(f"Failed to process avatar: {exc}") from exc
