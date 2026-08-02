"""Office document security and metadata stripping."""

import logging
import shutil
import subprocess
import xml.etree.ElementTree as StdET
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from defusedxml import ElementTree as SafeET

from app.core.security.file_security._concurrency import (
    _make_temp_path,
    _shielded_to_thread,
    subprocess_guard,
)
from app.core.security.file_security._zip import (
    _ZIP_MAX_ENTRIES,
    _ZIP_MAX_ENTRY_BYTES,
    _ZIP_MAX_TOTAL_BYTES,
    _sanitize_zip_entry_name,
)
from app.core.security.file_security.errors import SanitizationError, UnsafeFileError
from app.core.security.sandbox import async_sandboxed_run

logger = logging.getLogger(__name__)


async def _run_exiftool(file_path: Path) -> subprocess.CompletedProcess[bytes]:
    async with subprocess_guard():
        return await async_sandboxed_run(
            ["exiftool", "-all=", "-overwrite_original", str(file_path)],
            rw_paths=[file_path],
            timeout=30,
        )


def _check_ole2_macros(source: bytes | Path) -> None:
    """Reject any legacy Office file containing VBA macros."""
    from oletools.olevba import VBA_Parser

    parser = None
    try:
        if isinstance(source, bytes):
            parser = VBA_Parser("file", data=source)
        else:
            parser = VBA_Parser(str(source))
        if parser.detect_vba_macros():
            raise UnsafeFileError("Macro-enabled legacy Office files are not supported.")
    except (UnsafeFileError, SanitizationError):
        raise
    except Exception as exc:
        logger.warning("OLE2 structure malformed, failing closed: %s", exc)
        raise SanitizationError(
            "File appears malformed or could not be validated for VBA content."
        ) from exc
    finally:
        if parser is not None:
            parser.close()


async def _strip_ole2_from_path(file_path: Path) -> Path:
    """Reject VBA and strip metadata from a copied legacy Office file."""
    await _shielded_to_thread(_check_ole2_macros, file_path)

    new_path = _make_temp_path()
    try:
        await _shielded_to_thread(shutil.copyfile, file_path, new_path)
        result = await _run_exiftool(new_path)
        if result.returncode != 0:
            logger.warning(
                "exiftool OLE2 metadata strip failed (rc=%d): %s",
                result.returncode,
                result.stderr[:500],
            )
            raise SanitizationError("Failed to sanitize legacy Office document metadata.")
        return new_path
    except BaseException:
        new_path.unlink(missing_ok=True)
        raise


_ODF_MIME_PREFIX = "application/vnd.oasis.opendocument."
_EPUB_PRIVATE_ELEMENTS = frozenset(
    {"creator", "contributor", "publisher", "date", "rights", "description", "subject"}
)
_EPUB_PRIVATE_META_NAMES = frozenset(
    {"author", "creator", "contributor", "publisher", "rights", "description", "subject"}
)
_MAX_PACKAGE_XML_BYTES = 1 * 1024 * 1024
_OOXML_ACTIVE_DIRECTORIES = frozenset({"activex", "embeddings"})
_OOXML_ACTIVE_FILES = frozenset({"vbaproject.bin", "vbadata.xml"})
_ALLOWED_HYPERLINK_SCHEMES = frozenset({"http", "https", "mailto"})


def _parse_package_xml(data: bytes, description: str):
    """Parse security-sensitive package XML and normalize parser failures."""
    try:
        return SafeET.fromstring(data)
    except Exception as exc:
        raise SanitizationError(f"Malformed {description} XML") from exc


def _strip_epub_package_metadata(data: bytes) -> bytes:
    """Remove personal metadata from an EPUB OPF package document."""
    if len(data) > _MAX_PACKAGE_XML_BYTES:
        raise SanitizationError("EPUB package metadata is too large")
    root = _parse_package_xml(data, "EPUB package")
    for parent in root.iter():
        for child in list(parent):
            local_name = child.tag.rsplit("}", 1)[-1].lower()
            if local_name in _EPUB_PRIVATE_ELEMENTS:
                parent.remove(child)
                continue
            if local_name != "meta":
                continue
            name = (child.attrib.get("name") or child.attrib.get("property") or "").lower()
            if any(private_name in name for private_name in _EPUB_PRIVATE_META_NAMES):
                parent.remove(child)
    return StdET.tostring(root, encoding="utf-8", xml_declaration=True)


def _is_ooxml_active_content(path: str) -> bool:
    """Return whether an OOXML entry can execute or embed active content."""
    parts = path.casefold().split("/")
    return parts[-1] in _OOXML_ACTIVE_FILES or any(
        part in _OOXML_ACTIVE_DIRECTORIES for part in parts
    )


def _relationship_attribute(relationship, name: str) -> str:
    wanted = name.casefold()
    return next(
        (
            value
            for attribute_name, value in relationship.attrib.items()
            if attribute_name.rsplit("}", 1)[-1].casefold() == wanted
        ),
        "",
    )


def _reject_external_ooxml_relationships(data: bytes, entry_name: str) -> None:
    """Reject external resources except explicitly allowed hyperlink schemes."""
    root = _parse_package_xml(data, f"OOXML relationship file '{entry_name}'")
    for relationship in root.iter():
        if relationship.tag.rsplit("}", 1)[-1].casefold() != "relationship":
            continue
        if _relationship_attribute(relationship, "targetmode").strip().casefold() != "external":
            continue

        relationship_type = _relationship_attribute(relationship, "type")
        target = _relationship_attribute(relationship, "target").strip()
        if not relationship_type.casefold().endswith("/hyperlink"):
            raise UnsafeFileError(
                f"OOXML relationship file '{entry_name}' contains a prohibited external relationship"
            )

        scheme = urlsplit(target).scheme.casefold()
        if scheme not in _ALLOWED_HYPERLINK_SCHEMES:
            raise UnsafeFileError(
                f"OOXML relationship file '{entry_name}' contains a prohibited "
                f"external hyperlink target: '{target}'"
            )


def _zip_strip_file(file_path: Path, new_path: Path, mime_type: str | None = None) -> None:
    """Validate and rewrite an OOXML, ODF, or EPUB package."""
    try:
        with (
            zipfile.ZipFile(file_path, "r") as source_archive,
            zipfile.ZipFile(new_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output_archive,
        ):
            items = source_archive.infolist()
            if len(items) > _ZIP_MAX_ENTRIES:
                raise SanitizationError(
                    f"ZIP archive contains too many entries (max {_ZIP_MAX_ENTRIES})"
                )

            is_odf = bool(mime_type and mime_type.startswith(_ODF_MIME_PREFIX))
            is_epub = mime_type == "application/epub+zip"
            is_ooxml = not is_odf and not is_epub
            if is_epub:
                items.sort(key=lambda item: _sanitize_zip_entry_name(item.filename) != "mimetype")

            if sum(item.file_size for item in items) > _ZIP_MAX_TOTAL_BYTES:
                raise SanitizationError("ZIP archive uncompressed content is too large")

            # Validate names and active content before writing any output entries.
            validated_items: list[tuple[zipfile.ZipInfo, str, str]] = []
            normalized_names: set[str] = set()
            for item in items:
                if item.file_size > _ZIP_MAX_ENTRY_BYTES:
                    raise SanitizationError(f"ZIP entry '{item.filename}' is too large")
                safe_name = _sanitize_zip_entry_name(item.filename)
                normalized_name = safe_name.casefold()
                if normalized_name in normalized_names:
                    raise SanitizationError(f"ZIP contains duplicate sanitized entry '{safe_name}'")
                normalized_names.add(normalized_name)
                if is_ooxml and _is_ooxml_active_content(safe_name):
                    raise UnsafeFileError(
                        f"OOXML package contains prohibited active content: '{safe_name}'"
                    )
                validated_items.append((item, safe_name, normalized_name))

            total_actual_written = 0
            for item, safe_name, normalized_name in validated_items:
                if normalized_name.startswith(("docprops/", "basic/", "scripts/")) or (
                    is_odf and normalized_name == "meta.xml"
                ):
                    continue

                new_info = zipfile.ZipInfo(filename=safe_name, date_time=item.date_time)
                new_info.compress_type = (
                    zipfile.ZIP_STORED if safe_name == "mimetype" else zipfile.ZIP_DEFLATED
                )

                if is_epub and normalized_name.endswith(".opf"):
                    with source_archive.open(item) as source:
                        package_data = source.read(_MAX_PACKAGE_XML_BYTES + 1)
                    if len(package_data) > _MAX_PACKAGE_XML_BYTES:
                        raise SanitizationError("EPUB package metadata is too large")
                    stripped_data = _strip_epub_package_metadata(package_data)
                    total_actual_written += len(stripped_data)
                    if total_actual_written > _ZIP_MAX_TOTAL_BYTES:
                        raise SanitizationError(
                            "ZIP archive actual uncompressed content exceeds total limit"
                        )
                    output_archive.writestr(new_info, stripped_data)
                    continue

                if is_ooxml and normalized_name.endswith(".rels"):
                    with source_archive.open(item) as source:
                        relationship_data = source.read(_MAX_PACKAGE_XML_BYTES + 1)
                    if len(relationship_data) > _MAX_PACKAGE_XML_BYTES:
                        raise SanitizationError(
                            f"OOXML relationship file '{safe_name}' is too large"
                        )
                    _reject_external_ooxml_relationships(relationship_data, safe_name)
                    total_actual_written += len(relationship_data)
                    if total_actual_written > _ZIP_MAX_TOTAL_BYTES:
                        raise SanitizationError(
                            "ZIP archive actual uncompressed content exceeds total limit"
                        )
                    output_archive.writestr(new_info, relationship_data)
                    continue

                with (
                    source_archive.open(item) as source,
                    output_archive.open(new_info, "w") as dest,
                ):
                    written = 0
                    while chunk := source.read(65536):
                        written += len(chunk)
                        total_actual_written += len(chunk)
                        if written > _ZIP_MAX_ENTRY_BYTES:
                            raise SanitizationError(
                                f"ZIP entry '{item.filename}' expanded beyond limit"
                            )
                        if total_actual_written > _ZIP_MAX_TOTAL_BYTES:
                            raise SanitizationError(
                                "ZIP archive actual uncompressed content exceeds total limit"
                            )
                        dest.write(chunk)
    except (UnsafeFileError, SanitizationError):
        raise
    except Exception as exc:
        raise SanitizationError("Failed to validate and sanitize document package") from exc


async def _strip_ooxml_from_path(file_path: Path, mime_type: str | None = None) -> Path:
    """Rewrite a document package and remove partial output on every failure."""
    new_path = _make_temp_path()
    try:
        await _shielded_to_thread(_zip_strip_file, file_path, new_path, mime_type)
        return new_path
    except BaseException:
        new_path.unlink(missing_ok=True)
        raise
