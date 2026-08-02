"""Shared typed exception hierarchy for file security, sanitization, and compression."""


class SanitizationError(ValueError):
    """Base exception for file sanitization and metadata stripping errors."""


class UnsafeFileError(SanitizationError):
    """Raised when a file contains active or unsafe content (macros, scripts, active objects)."""


class CompressionError(ValueError):
    """Base exception for file compression errors."""
