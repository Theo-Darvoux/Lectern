"""Shared constants for the upload pipeline."""

from typing import Final

__all__ = ["MAGIC_HEADER_SIZE"]

# Number of bytes to read for magic-byte MIME detection
MAGIC_HEADER_SIZE: Final[int] = 2048
