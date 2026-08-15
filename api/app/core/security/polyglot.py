"""Polyglot file detection."""

import logging
import zipfile
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
_ZIP_EOCD_SEARCH_BYTES = 22 + 65_535
_ZIP_METADATA_READ_BUDGET = 1 * 1024 * 1024

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


class _ZipMetadataBudgetExceededError(RuntimeError):
    """ZIP metadata parsing exceeded its fixed I/O budget."""


class _BudgetedZipReader:
    """Random-access file adapter that bounds bytes read by ``zipfile``."""

    def __init__(self, file_obj: BinaryIO, max_read_bytes: int) -> None:
        self._file_obj = file_obj
        self._remaining = max_read_bytes

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            current = self._file_obj.tell()
            end = self._file_obj.seek(0, 2)
            self._file_obj.seek(current)
            size = end - current
        if size > self._remaining:
            raise _ZipMetadataBudgetExceededError
        data = self._file_obj.read(size)
        self._remaining -= len(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file_obj.seek(offset, whence)

    def tell(self) -> int:
        return self._file_obj.tell()

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


def _allowed_families(mime: str) -> set[str]:
    """Return the set of extra format families allowed for *mime*."""
    for prefix, families in _ALLOWED_EXTRA_FAMILIES.items():
        if mime.startswith(prefix):
            return families
    return set()


def _has_extractable_zip_payload(file_obj: BinaryIO, file_size: int) -> bool:
    """Return whether the file contains an appended ZIP accepted by ``zipfile``.

    A cheap EOCD scan avoids invoking the parser for ordinary media. The parser
    itself reads through a fixed budget so a forged central-directory size cannot
    force an attacker-sized allocation. Exceeding that budget is treated as
    suspicious because archive metadata larger than the budget is already beyond
    this upload pipeline's practical archive limits.
    """
    tail_size = min(file_size, _ZIP_EOCD_SEARCH_BYTES)
    file_obj.seek(file_size - tail_size)
    if _ZIP_EOCD not in file_obj.read(tail_size):
        return False

    file_obj.seek(0)
    reader = _BudgetedZipReader(file_obj, _ZIP_METADATA_READ_BUDGET)
    try:
        with zipfile.ZipFile(reader) as archive:
            entries = archive.infolist()
            if not entries:
                return True

            # ``infolist`` validates the central directory. Opening at least one
            # member additionally validates its local-file header without reading
            # or decompressing the member body.
            for entry in entries:
                try:
                    with archive.open(entry):
                        return True
                except RuntimeError:
                    # ``ZipFile.open`` validates the local header before it
                    # reports that an encrypted member needs a password.
                    if entry.flag_bits & 0x1:
                        return True
                    continue
                except (
                    zipfile.BadZipFile,
                    NotImplementedError,
                    ValueError,
                    OSError,
                ):
                    continue
            return False
    except _ZipMetadataBudgetExceededError:
        return True
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        return False


def check_polyglot(file_path: Path, detected_mime: str) -> None:
    """Raise ValueError if *file_path* shows polyglot characteristics.

    Header checks reject files whose leading magic conflicts with the detected
    MIME. Appended ZIP detection accepts the same trailing-data variants as the
    standard-library ZIP reader while rejecting marker-only false positives.
    """
    try:
        file_size = file_path.stat().st_size
        if file_size < 4:
            return

        with open(file_path, "rb") as file_obj:
            header = file_obj.read(256)
            has_appended_zip = _has_extractable_zip_payload(file_obj, file_size)
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
            "valid ZIP End-of-Central-Directory and an extractable ZIP payload "
            "at its tail (possible appended ZIP/JAR/APK polyglot)"
        )
