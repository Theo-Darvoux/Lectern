"""Public, lazily loaded file-security API.

Keeping imports lazy prevents optional/native processors and Redis/database
initialization from running merely because a format-specific helper is imported.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "strip_metadata_file",
    "compress_file_path",
    "CompressResultPath",
    "check_pdf_safety",
    "check_svg_safety",
    "check_svg_safety_stream",
    "SvgSecurityError",
    "get_uncompressed_size",
    "run_managed_subprocess",
    "VIDEO_COMPRESS_THRESHOLD",
    "MAX_GIF_FRAMES",
    "MAX_GIF_TOTAL_PIXELS",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "strip_metadata_file": ("app.core.security.file_security.strip", "strip_metadata_file"),
    "compress_file_path": ("app.core.security.file_security.compress", "compress_file_path"),
    "CompressResultPath": ("app.core.security.file_security.compress", "CompressResultPath"),
    "check_pdf_safety": ("app.core.security.file_security._pdf", "check_pdf_safety"),
    "check_svg_safety": ("app.core.security.file_security._svg", "check_svg_safety"),
    "check_svg_safety_stream": (
        "app.core.security.file_security._svg",
        "check_svg_safety_stream",
    ),
    "SvgSecurityError": ("app.core.security.file_security._svg", "SvgSecurityError"),
    "get_uncompressed_size": (
        "app.core.security.file_security._zip",
        "get_uncompressed_size",
    ),
    "run_managed_subprocess": (
        "app.core.security.file_security._concurrency",
        "run_managed_subprocess",
    ),
    "VIDEO_COMPRESS_THRESHOLD": (
        "app.core.security.file_security._audio_video",
        "VIDEO_COMPRESS_THRESHOLD",
    ),
    "MAX_GIF_FRAMES": ("app.core.security.file_security._image", "MAX_GIF_FRAMES"),
    "MAX_GIF_TOTAL_PIXELS": (
        "app.core.security.file_security._image",
        "MAX_GIF_TOTAL_PIXELS",
    ),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
