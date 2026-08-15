"""Shared constants for the upload pipeline."""

from typing import Final

__all__ = [
    "PRIVILEGED_ROLES",
    "MAGIC_HEADER_SIZE",
]

# Roles that bypass standard limits (rate limiting, quota, etc.)
PRIVILEGED_ROLES: Final[frozenset[str]] = frozenset({"moderator", "bureau", "vieux"})

# Number of bytes to read for magic-byte MIME detection
MAGIC_HEADER_SIZE: Final[int] = 2048
