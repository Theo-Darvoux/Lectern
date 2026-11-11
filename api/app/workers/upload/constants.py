import logging
import shutil
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_SCAN_CACHE_PREFIX = "upload:scanned:"

# Pipeline stage definitions
_STAGES = [
    ("scanning", "Scanning for malware", 0.85),
    ("finalizing", "Finalising upload", 0.15),
]
_STAGE_TOTAL = len(_STAGES)
_STAGE_BASES = [sum(w for _, _, w in _STAGES[:i]) for i in range(_STAGE_TOTAL)]

# Longer than the pipeline's own deadline so the pipeline can report its timeout.
UPLOAD_JOB_TIMEOUT_SECONDS = settings.upload_pipeline_max_seconds + 60

_MAX_ARQ_RETRIES = 3


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
