"""Polyglot file detection."""

import logging
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


class HeaderMagic(NamedTuple):
    label: str
    magic: bytes
    family: str


# Header magic signatures used for format cross-checks.
_HEADER_MAGIC: list[HeaderMagic] = [
    HeaderMagic("zip", b"PK\x03\x04", "archive"),
    HeaderMagic("zip_empty", b"PK\x05\x06", "archive"),
    HeaderMagic("zip_spanned", b"PK\x07\x08", "archive"),
    HeaderMagic("pdf", b"%PDF-", "pdf"),
    HeaderMagic("html_doctype", b"<!DOCTYPE ", "html"),
    HeaderMagic("html_tag", b"<html", "html"),
    HeaderMagic("html_tag_uc", b"<HTML", "html"),
    HeaderMagic("script_tag", b"<script", "html"),
    HeaderMagic("pe_exe", b"MZ", "executable"),
    HeaderMagic("elf_exe", b"\x7fELF", "executable"),
    HeaderMagic("java_class", b"\xca\xfe\xba\xbe", "executable"),
]

# Signature of a ZIP End-of-Central-Directory record.
_ZIP_EOCD = b"PK\x05\x06"

_ALLOWED_EXTRA_FAMILIES: dict[str, set[str]] = {
    "image/": set(),
    "video/": set(),
    "audio/": set(),
    "application/pdf": {"pdf"},
    "application/zip": {"archive"},
    "application/x-zip": {"archive"},
    "text/html": {"html"},
    "text/xml": {"html"},
    "application/xml": {"html"},
    "application/vnd.openxmlformats-": {"archive"},  # Office formats are ZIPs
    "application/vnd.oasis.opendocument.": {"archive"},  # ODS/ODT/ODP are ZIPs
    "application/epub+zip": {"archive"},  # EPUB is a ZIP
}


def _allowed_families(mime: str) -> set[str]:
    """Return the set of extra format families allowed for *mime*."""
    for prefix, families in _ALLOWED_EXTRA_FAMILIES.items():
        if mime.startswith(prefix):
            return families
    return set()


def check_polyglot(file_path: Path, detected_mime: str) -> None:
    """Raise ValueError if *file_path* shows polyglot characteristics.

    Args:
        file_path: Path to the local temp file.
        detected_mime: The MIME type determined by magic-byte detection.

    Raises:
        ValueError: With a description of the polyglot pattern,
            or if the security check itself failed to execute.
    """
    try:
        file_size = file_path.stat().st_size
        if file_size < 4:
            return

        with open(file_path, "rb") as f:
            if file_size <= 512:
                data = f.read()
                header, tail = data, data
            else:
                header = f.read(256)
                # ZIP EOCD is 22 bytes + up to 65,535 bytes comment.
                tail_size = min(file_size, 65558)
                f.seek(-tail_size, 2)
                tail = f.read(tail_size)
    except OSError as exc:
        logger.error("Security critical: polyglot check failed to read %s: %s", file_path, exc)
        raise ValueError("Security validation failed: structural check unreachable.") from exc

    allowed = _allowed_families(detected_mime)

    for label, magic, family in _HEADER_MAGIC:
        if family in allowed:
            continue
        if header.startswith(magic):
            raise ValueError(
                f"Polyglot file detected: {detected_mime!r} file starts with "
                f"{label!r} magic bytes (format family: {family!r})"
            )

    if "archive" not in allowed and _ZIP_EOCD in tail:
        raise ValueError(
            f"Polyglot file detected: {detected_mime!r} file contains a "
            "ZIP End-of-Central-Directory record at its tail "
            "(possible appended ZIP/JAR/APK polyglot)"
        )
