import logging
import shutil
from pathlib import Path

from app.core.mimetypes import ZIP_MIME_TYPES

logger = logging.getLogger(__name__)

_STATUS_CACHE_PREFIX = "upload:status:"
_SCAN_CACHE_PREFIX = "upload:scanned:"
_SHA256_CACHE_PREFIX = "upload:sha256:"

# Pipeline stage definitions
_STAGES = [
    ("scanning", "Scanning for malware", 0.35),
    ("stripping", "Removing private metadata", 0.20),
    ("compressing", "Optimising file size", 0.20),
    ("thumbnailing", "Generating previews", 0.15),
    ("finalizing", "Finalising upload", 0.10),
]
_STAGE_TOTAL = len(_STAGES)
_STAGE_BASES = [sum(w for _, _, w in _STAGES[:i]) for i in range(_STAGE_TOTAL)]

_COMPRESSION_TIMEOUTS: dict[str, float] = {
    "application/pdf": 300.0,
    "video/mp4": 1200.0,
    "video/webm": 1200.0,
    "audio/": 60.0,
    "image/": 30.0,
    "text/": 15.0,
    # ZIP-based office formats (OOXML, ODF, EPUB) now compress embedded images
    # via Pillow, which is CPU-bound per entry — large presentations need headroom.
    "zip": 300.0,
    "default": 90.0,
}

_CANCEL_KEY_PREFIX = "upload:cancel:"
_MAX_ARQ_RETRIES = 3


def _compression_timeout(mime_type: str) -> float:
    """Return the compression deadline (seconds) for the given MIME type."""
    if mime_type in _COMPRESSION_TIMEOUTS:
        return _COMPRESSION_TIMEOUTS[mime_type]
    if mime_type in ZIP_MIME_TYPES:
        return _COMPRESSION_TIMEOUTS["zip"]
    for prefix, timeout in _COMPRESSION_TIMEOUTS.items():
        if prefix.endswith("/") and mime_type.startswith(prefix):
            return timeout
    return _COMPRESSION_TIMEOUTS["default"]


def _overall(stage_index: int, stage_percent: float) -> float:
    """Compute overall progress [0.0, 1.0] from stage index + within-stage percent."""
    base = _STAGE_BASES[stage_index]
    weight = _STAGES[stage_index][2]
    return round(base + weight * stage_percent, 4)


def ensure_disk_space(path: Path, required_free_bytes: int) -> None:
    """Verify sufficient disk space is available."""
    usage = shutil.disk_usage(path.parent if path.is_file() else path)
    if usage.free < required_free_bytes:
        logger.error(
            "Worker disk space critically low: %d bytes free, %d required.",
            usage.free,
            required_free_bytes,
        )
        raise RuntimeError(f"Insufficient disk space for operation at {path}")
