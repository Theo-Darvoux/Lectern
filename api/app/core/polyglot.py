"""Polyglot file detection.

A polyglot file is simultaneously valid under two or more format parsers.
Attackers use polyglots to bypass MIME-based security checks — for example,
a file that parses as a JPEG in the upload validator but as JavaScript when
rendered by a browser, or a JPEG with a ZIP archive appended at the end
(JZBOMB / JPEG+ZIP).

Checks performed by :func:`check_polyglot`:

1. **Header magic cross-check** — does the file start with magic bytes from a
   format incompatible with the declared MIME type?
2. **ZIP tail check** — ZIP archives always end with a 22-byte
   End-of-Central-Directory (EOCD) record starting with ``PK\\x05\\x06``.
   An image or PDF with a ZIP appended will contain this signature near its
   end, even though it begins with valid image/PDF magic bytes.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# (label, magic_prefix, format_family) triples used for header checks.
# ``format_family`` groups semantically related MIME types so we do not flag
# legitimate subtypes (e.g. application/zip vs application/x-zip-compressed).
_HEADER_MAGIC: list[tuple[str, bytes, str]] = [
    ("zip", b"PK\x03\x04", "archive"),
    ("zip_empty", b"PK\x05\x06", "archive"),
    ("zip_spanned", b"PK\x07\x08", "archive"),
    ("pdf", b"%PDF-", "pdf"),
    ("html_doctype", b"<!DOCTYPE ", "html"),
    ("html_tag", b"<html", "html"),
    ("html_tag_uc", b"<HTML", "html"),
    ("script_tag", b"<script", "html"),
    ("pe_exe", b"MZ", "executable"),
    ("elf_exe", b"\x7fELF", "executable"),
    ("java_class", b"\xca\xfe\xba\xbe", "executable"),
]

# Signature of a ZIP End-of-Central-Directory record.
_ZIP_EOCD = b"PK\x05\x06"

# Per-MIME-prefix: which additional format families are expected/allowed.
# An empty set means *no* other format magic is acceptable.
_ALLOWED_EXTRA_FAMILIES: dict[str, set[str]] = {
    "image/": set(),
    "video/": set(),
    "audio/": set(),
    "application/pdf": {"pdf"},  # PDF magic inside a PDF is fine
    "application/zip": {"archive"},
    "application/x-zip": {"archive"},
    "text/html": {"html"},  # HTML magic expected in HTML files
    "text/xml": {"html"},  # XML can look like HTML (<?xml)
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

    This is a fast structural check; it does *not* fully parse every format.
    STRICT FAIL-CLOSED: If the file cannot be read, it MUST be rejected.

    Args:
        file_path: Path to the local temp file.
        detected_mime: The MIME type determined by magic-byte detection.

    Raises:
        ValueError: With a human-readable description of the polyglot pattern,
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
                f.seek(-256, 2)
                tail = f.read(256)
    except OSError as exc:
        logger.error("Security critical: polyglot check failed to read %s: %s", file_path, exc)
        raise ValueError("Security validation failed: structural check unreachable.") from exc

    allowed = _allowed_families(detected_mime)

    # ── Header magic cross-check ─────────────────────────────────────────────
    for label, magic, family in _HEADER_MAGIC:
        if family in allowed:
            continue
        if header.startswith(magic):
            raise ValueError(
                f"Polyglot file detected: {detected_mime!r} file starts with "
                f"{label!r} magic bytes (format family: {family!r})"
            )

    # ── ZIP tail check (appended archive polyglot) ───────────────────────────
    # Archives are OK in archive MIME types; for everything else the presence
    # of a ZIP EOCD record near the end is suspicious.
    if "archive" not in allowed and _ZIP_EOCD in tail:
        raise ValueError(
            f"Polyglot file detected: {detected_mime!r} file contains a "
            "ZIP End-of-Central-Directory record at its tail "
            "(possible appended ZIP/JAR/APK polyglot)"
        )
