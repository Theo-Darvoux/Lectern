"""Prometheus metrics for the upload pipeline.

All metrics are registered in a single CollectorRegistry so tests can use
isolated registries without polluting the default global one.

Usage (worker/endpoint code)::

    from app.core.observability.metrics import (
        upload_pipeline_total,
        upload_pipeline_duration,
        upload_file_size,
        upload_compression_ratio,
        upload_scan_duration,
    )

    upload_pipeline_total.labels(status="clean", mime_category="image").inc()
    upload_pipeline_duration.labels(status="clean", mime_category="image").observe(3.14)
"""

from prometheus_client import CollectorRegistry, Counter, Histogram

__all__ = [
    "REGISTRY",
    "upload_pipeline_total",
    "upload_webhook_total",
    "upload_pipeline_duration",
    "upload_scan_duration",
    "upload_file_size",
    "upload_compression_ratio",
    "mime_category",
]

REGISTRY = CollectorRegistry(auto_describe=True)

upload_pipeline_total = Counter(
    "lectern_upload_pipeline_total",
    "Total uploads processed by the background pipeline, labelled by outcome and MIME category.",
    labelnames=["status", "mime_category"],
    registry=REGISTRY,
)
"""Labels:
- ``status``: ``clean`` | ``malicious`` | ``failed`` | ``cas_hit``
- ``mime_category``: coarse MIME group (image, video, audio, document, text, office, other)
"""

upload_webhook_total = Counter(
    "lectern_upload_webhook_total",
    "Total webhook dispatch attempts, labelled by outcome.",
    labelnames=["outcome"],
    registry=REGISTRY,
)
"""Labels: ``outcome``: ``success`` | ``http_error`` | ``network_error`` | ``skipped``"""

_PIPELINE_DURATION_BUCKETS = (0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600)
_SCAN_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60)
_FILE_SIZE_BUCKETS = (
    10_000,
    100_000,
    500_000,
    1_048_576,
    5_242_880,
    10_485_760,
    52_428_800,
    104_857_600,
    524_288_000,
)
_COMPRESSION_RATIO_BUCKETS = (0.5, 0.75, 0.9, 0.95, 1.0, 1.05, 1.1, 1.25, 1.5, 2.0, 5.0)

upload_pipeline_duration = Histogram(
    "lectern_upload_pipeline_duration_seconds",
    "End-to-end pipeline duration from job start to terminal status.",
    labelnames=["status", "mime_category"],
    buckets=_PIPELINE_DURATION_BUCKETS,
    registry=REGISTRY,
)

upload_scan_duration = Histogram(
    "lectern_upload_scan_duration_seconds",
    "Duration of the malware scan stage only.",
    labelnames=["mime_category"],
    buckets=_SCAN_DURATION_BUCKETS,
    registry=REGISTRY,
)

upload_file_size = Histogram(
    "lectern_upload_file_size_bytes",
    "Original file size at the time of upload (before pipeline processing).",
    labelnames=["mime_category"],
    buckets=_FILE_SIZE_BUCKETS,
    registry=REGISTRY,
)

upload_compression_ratio = Histogram(
    "lectern_upload_compression_ratio",
    "Ratio of original size to final size (> 1 means size was reduced).",
    labelnames=["mime_category"],
    buckets=_COMPRESSION_RATIO_BUCKETS,
    registry=REGISTRY,
)


_DOCUMENT_MIMES = frozenset(("application/pdf", "application/epub+zip", "image/vnd.djvu"))
_OFFICE_PREFIXES = (
    "application/vnd.openxmlformats",
    "application/msword",
    "application/vnd.ms-",
)


def mime_category(mime_type: str) -> str:
    """Map a full MIME type string to a coarse label used in metric tags."""
    if mime_type in _DOCUMENT_MIMES:
        return "document"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("text/"):
        return "text"
    if mime_type.startswith(_OFFICE_PREFIXES):
        return "office"
    return "other"
