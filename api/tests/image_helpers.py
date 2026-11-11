"""Byte-level wrapper over the path-based image sanitizer, for tests."""

from app.core.security.file_security._image import _strip_image_from_path
from app.core.security.processing_paths import make_processing_temp_path


def strip_image_metadata(data: bytes) -> bytes:
    source = make_processing_temp_path(suffix=".img")
    source.write_bytes(data)
    try:
        stripped = _strip_image_from_path(source)
        try:
            return stripped.read_bytes()
        finally:
            stripped.unlink(missing_ok=True)
    finally:
        source.unlink(missing_ok=True)
