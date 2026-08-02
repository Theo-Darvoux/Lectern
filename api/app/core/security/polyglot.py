"""Polyglot file detection."""

import logging
import struct
from pathlib import Path
from typing import BinaryIO, NamedTuple

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

_ZIP_EOCD = b"PK\x05\x06"
_ZIP_CENTRAL_DIRECTORY = b"PK\x01\x02"
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP_EOCD_STRUCT = struct.Struct("<4s4H2LH")
_ZIP_EOCD_SIZE = _ZIP_EOCD_STRUCT.size
_ZIP_MAX_COMMENT_BYTES = 65_535

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


def _has_valid_zip_eocd(file_obj: BinaryIO, file_size: int) -> bool:
    """Validate a standard appended ZIP EOCD without parsing attacker-sized indexes."""
    tail_size = min(file_size, _ZIP_EOCD_SIZE + _ZIP_MAX_COMMENT_BYTES)
    file_obj.seek(file_size - tail_size)
    tail = file_obj.read(tail_size)
    tail_start = file_size - len(tail)

    search_end = len(tail)
    while True:
        position = tail.rfind(_ZIP_EOCD, 0, search_end)
        if position < 0:
            return False
        search_end = position
        if position + _ZIP_EOCD_SIZE > len(tail):
            continue

        (
            _signature,
            disk_number,
            central_directory_disk,
            entries_on_disk,
            total_entries,
            central_directory_size,
            central_directory_offset,
            comment_length,
        ) = _ZIP_EOCD_STRUCT.unpack_from(tail, position)

        if position + _ZIP_EOCD_SIZE + comment_length != len(tail):
            continue
        if disk_number != 0 or central_directory_disk != 0:
            continue

        eocd_offset = tail_start + position
        uses_zip64 = (
            entries_on_disk == 0xFFFF
            or total_entries == 0xFFFF
            or central_directory_size == 0xFFFFFFFF
            or central_directory_offset == 0xFFFFFFFF
        )
        if uses_zip64:
            locator_offset = eocd_offset - 20
            if locator_offset >= 0:
                file_obj.seek(locator_offset)
                if file_obj.read(4) == _ZIP64_LOCATOR:
                    return True
            continue

        if entries_on_disk != total_entries:
            continue
        archive_start = eocd_offset - central_directory_size - central_directory_offset
        if archive_start < 0:
            continue

        if total_entries == 0:
            if central_directory_size == 0 and central_directory_offset == 0:
                return True
            continue

        central_directory_position = archive_start + central_directory_offset
        if central_directory_position < archive_start or central_directory_position >= eocd_offset:
            continue
        file_obj.seek(central_directory_position)
        if file_obj.read(4) == _ZIP_CENTRAL_DIRECTORY:
            return True


def check_polyglot(file_path: Path, detected_mime: str) -> None:
    """Raise ValueError if *file_path* shows polyglot characteristics.

    Header checks reject files whose leading magic conflicts with the detected
    MIME. Appended ZIP detection validates EOCD fields and central-directory
    placement; a raw four-byte sequence in compressed media is not sufficient.
    """
    try:
        file_size = file_path.stat().st_size
        if file_size < 4:
            return

        with open(file_path, "rb") as file_obj:
            header = file_obj.read(256)
            has_appended_zip = _has_valid_zip_eocd(file_obj, file_size)
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

    if "archive" not in allowed and has_appended_zip:
        raise ValueError(
            f"Polyglot file detected: {detected_mime!r} file contains a "
            "valid ZIP End-of-Central-Directory record at its tail "
            "(possible appended ZIP/JAR/APK polyglot)"
        )
