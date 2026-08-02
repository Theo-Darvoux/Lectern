"""Office document security and metadata stripping."""

import logging
import posixpath
import re
import shutil
import subprocess
import xml.etree.ElementTree as StdET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import mutagen
from defusedxml import ElementTree as SafeET

from app.core.security.async_utils import shielded_to_thread as _shielded_to_thread
from app.core.security.file_security._concurrency import (
    image_guard,
    subprocess_guard,
)
from app.core.security.file_security._svg import check_svg_safety
from app.core.security.file_security._zip import (
    _ZIP_MAX_ENTRIES,
    _ZIP_MAX_ENTRY_BYTES,
    _ZIP_MAX_TOTAL_BYTES,
    _read_zip_entry_bounded,
    _register_zip_name,
    _sanitize_embedded_image,
    _sanitize_zip_entry_name,
    _sanitized_zip_info,
    _validate_zip_info,
)
from app.core.security.file_security.errors import SanitizationError, UnsafeFileError
from app.core.security.processing_paths import (
    make_processing_temp_path as _make_temp_path,
)
from app.core.security.processing_paths import (
    processing_temp_dir,
)
from app.core.security.sandbox import async_sandboxed_run

logger = logging.getLogger(__name__)


async def _run_exiftool(
    file_path: Path,
    writable_dir: Path,
) -> subprocess.CompletedProcess[bytes]:
    """Run ExifTool with a writable directory for its atomic replacement file."""

    async with subprocess_guard():
        return await async_sandboxed_run(
            ["exiftool", "-all=", "-overwrite_original", str(file_path)],
            rw_paths=[writable_dir],
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


_OLE2_SUFFIXES = {
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
}


async def _strip_ole2_from_path(file_path: Path, mime_type: str | None = None) -> Path:
    """Reject VBA and strip metadata from a legacy Office file."""

    await _shielded_to_thread(_check_ole2_macros, file_path)
    suffix = _OLE2_SUFFIXES.get(mime_type or "", file_path.suffix or ".doc")
    final_path = _make_temp_path(suffix=suffix)
    try:
        with processing_temp_dir(prefix="ole2-strip-") as work_dir:
            working_path = work_dir / f"document{suffix}"
            await _shielded_to_thread(shutil.copyfile, file_path, working_path)
            result = await _run_exiftool(working_path, work_dir)
            if result.returncode != 0:
                logger.warning(
                    "exiftool OLE2 metadata strip failed (rc=%d): %s",
                    result.returncode,
                    result.stderr[:500],
                )
                raise SanitizationError("Failed to sanitize legacy Office document metadata.")
            await _shielded_to_thread(shutil.copyfile, working_path, final_path)
        return final_path
    except BaseException:
        final_path.unlink(missing_ok=True)
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
_PACKAGE_SUFFIXES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/epub+zip": ".epub",
}
_PACKAGE_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".bmp"}
)
_EPUB_IMAGE_MEDIA_TYPES = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
    "image/tiff": "TIFF",
    "image/bmp": "BMP",
}
_EPUB_MARKUP_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})
_EPUB_CSS_MEDIA_TYPES = frozenset({"text/css"})
_EPUB_MEDIA_TYPES = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "audio/wav": ".wav",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}
_PACKAGE_MEDIA_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".mp4", ".mov", ".aac", ".ogg", ".opus", ".flac", ".wav", ".wma", ".wmv"}
)
_UNSUPPORTED_EMBEDDED_MEDIA_EXTENSIONS = frozenset({".webm", ".avi"})
_EPUB_BLOCKED_PROPERTIES = frozenset({"scripted", "remote-resources"})
_EPUB_BLOCKED_ELEMENTS = frozenset({"script", "iframe", "object", "embed", "foreignobject", "form"})
_DANGEROUS_URI_RE = re.compile(
    r"^(?:javascript|vbscript|data\s*:\s*(?:text/html|application/))",
    re.IGNORECASE,
)
_CSS_ESCAPE_RE = re.compile(r"\\([0-9a-fA-F]{1,6})(?:\s)?|\\(.)", re.DOTALL)


def _parse_package_xml(data: bytes, description: str):
    """Parse security-sensitive package XML and normalize parser failures."""
    try:
        return SafeET.fromstring(data)
    except Exception as exc:
        raise SanitizationError(f"Malformed {description} XML") from exc


def _resolve_epub_href(opf_name: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise UnsafeFileError(f"EPUB manifest contains an external resource: '{href}'")
    decoded_path = unquote(parsed.path)
    if not decoded_path:
        raise SanitizationError("EPUB manifest contains an empty resource path")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(opf_name), decoded_path))
    if joined.startswith("../") or joined == ".." or joined.startswith("/"):
        raise UnsafeFileError(f"EPUB manifest resource escapes the package: '{href}'")
    return _sanitize_zip_entry_name(joined).casefold()


def _strip_epub_package_metadata(
    data: bytes,
    opf_name: str = "package.opf",
) -> tuple[bytes, dict[str, str]]:
    """Strip private OPF metadata and return its validated manifest mapping."""

    if len(data) > _MAX_PACKAGE_XML_BYTES:
        raise SanitizationError("EPUB package metadata is too large")
    root = _parse_package_xml(data, "EPUB package")
    manifest: dict[str, str] = {}
    manifest_ids: set[str] = set()

    for parent in root.iter():
        for child in list(parent):
            local_name = child.tag.rsplit("}", 1)[-1].lower()
            if local_name in _EPUB_PRIVATE_ELEMENTS:
                parent.remove(child)
                continue
            if local_name == "meta":
                name = (child.attrib.get("name") or child.attrib.get("property") or "").lower()
                if any(private_name in name for private_name in _EPUB_PRIVATE_META_NAMES):
                    parent.remove(child)
                    continue
            if local_name != "item":
                continue

            item_id = child.attrib.get("id", "").strip()
            href = child.attrib.get("href", "").strip()
            media_type = child.attrib.get("media-type", "").strip().casefold()
            properties = frozenset(child.attrib.get("properties", "").casefold().split())
            if not item_id or not href or not media_type:
                raise SanitizationError("EPUB manifest item is missing id, href, or media-type")
            if properties & _EPUB_BLOCKED_PROPERTIES:
                blocked = ", ".join(sorted(properties & _EPUB_BLOCKED_PROPERTIES))
                raise UnsafeFileError(f"EPUB manifest item uses prohibited properties: {blocked}")
            if item_id in manifest_ids:
                raise SanitizationError(f"EPUB manifest contains duplicate id '{item_id}'")
            manifest_ids.add(item_id)
            resource_name = _resolve_epub_href(opf_name, href)
            if resource_name in manifest:
                raise SanitizationError(f"EPUB manifest contains duplicate resource '{href}'")
            manifest[resource_name] = media_type

    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].casefold() != "itemref":
            continue
        idref = element.attrib.get("idref", "").strip()
        if not idref or idref not in manifest_ids:
            raise SanitizationError(f"EPUB spine references unknown manifest id '{idref}'")

    return StdET.tostring(root, encoding="utf-8", xml_declaration=True), manifest


def _decode_css_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group(1):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return ""
        return match.group(2) or ""

    return _CSS_ESCAPE_RE.sub(replace, value)


def _validate_epub_css(data: bytes, entry_name: str) -> None:
    try:
        css = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SanitizationError(f"EPUB CSS '{entry_name}' is not valid UTF-8") from exc
    normalized = _decode_css_escapes(re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)).casefold()
    if "@import" in normalized or "expression(" in normalized:
        raise UnsafeFileError(f"EPUB CSS '{entry_name}' contains prohibited active content")
    if "javascript:" in normalized or "vbscript:" in normalized or "data:text/html" in normalized:
        raise UnsafeFileError(f"EPUB CSS '{entry_name}' contains a dangerous URI")
    for match in re.finditer(r"url\s*\((.*?)\)", normalized, flags=re.DOTALL):
        target = match.group(1).strip().strip("\"'")
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or target.startswith("//"):
            raise UnsafeFileError(f"EPUB CSS '{entry_name}' references an external resource")


def _validate_epub_markup(data: bytes, entry_name: str) -> None:
    root = _parse_package_xml(data, f"EPUB markup '{entry_name}'")
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1].casefold()
        if local in _EPUB_BLOCKED_ELEMENTS:
            raise UnsafeFileError(f"EPUB markup '{entry_name}' contains <{local}>")
        for raw_name, raw_value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].casefold()
            value = _decode_css_escapes(raw_value.strip())
            if name.startswith("on"):
                raise UnsafeFileError(f"EPUB markup '{entry_name}' contains an event handler")
            if _DANGEROUS_URI_RE.match(value):
                raise UnsafeFileError(f"EPUB markup '{entry_name}' contains a dangerous URI")
            if name == "style":
                _validate_epub_css(value.encode(), entry_name)
            resource_attribute = name in {"src", "poster", "data", "action"} or (
                name == "href" and local not in {"a", "area"}
            )
            if resource_attribute:
                parsed = urlsplit(value)
                if parsed.scheme or parsed.netloc or value.startswith("//"):
                    raise UnsafeFileError(
                        f"EPUB markup '{entry_name}' references an external resource"
                    )
        if local == "style" and element.text:
            _validate_epub_css(element.text.encode(), entry_name)
        if local == "meta" and element.attrib.get("http-equiv", "").casefold() == "refresh":
            raise UnsafeFileError(f"EPUB markup '{entry_name}' contains meta refresh")


def _validate_odf_xml_stream(source, description: str) -> None:
    """Reject ODF script/event payloads while allowing the standard empty scripts container."""

    blocked_elements = {"script", "event-listener", "binary-data"}
    try:
        for event, element in SafeET.iterparse(source, events=("start", "end")):
            if event == "start":
                local = element.tag.rsplit("}", 1)[-1].casefold()
                if local in blocked_elements:
                    raise UnsafeFileError(
                        f"ODF {description} contains prohibited <{local}> content"
                    )
                for raw_name, raw_value in element.attrib.items():
                    name = raw_name.rsplit("}", 1)[-1].casefold()
                    value = raw_value.strip().casefold()
                    if name.startswith("on") or "macro" in name:
                        raise UnsafeFileError(f"ODF {description} contains a macro/event attribute")
                    if value.startswith(("macro:", "vnd.sun.star.script:")):
                        raise UnsafeFileError(f"ODF {description} contains a script URI")
            elif event == "end":
                element.clear()
    except (UnsafeFileError, SanitizationError):
        raise
    except Exception as exc:
        raise SanitizationError(f"Malformed ODF {description} XML") from exc


def _sanitize_embedded_media(
    data: bytes,
    entry_name: str,
    *,
    suffix_override: str | None = None,
) -> bytes:
    """Remove tags from supported embedded media without transcoding payload data."""

    suffix = (suffix_override or Path(entry_name).suffix).casefold()
    if suffix in _UNSUPPORTED_EMBEDDED_MEDIA_EXTENSIONS:
        raise UnsafeFileError(f"Embedded media format '{suffix}' is not safely sanitizable")
    if suffix not in _PACKAGE_MEDIA_EXTENSIONS:
        return data

    temp_path = _make_temp_path(suffix=suffix)
    try:
        temp_path.write_bytes(data)
        media_loaders = {
            ".mp3": lambda path: __import__("mutagen.mp3", fromlist=["MP3"]).MP3(path),
            ".flac": lambda path: __import__("mutagen.flac", fromlist=["FLAC"]).FLAC(path),
            ".ogg": lambda path: __import__("mutagen.oggvorbis", fromlist=["OggVorbis"]).OggVorbis(
                path
            ),
            ".opus": lambda path: __import__("mutagen.oggopus", fromlist=["OggOpus"]).OggOpus(path),
            ".wav": lambda path: __import__("mutagen.wave", fromlist=["WAVE"]).WAVE(path),
            ".mp4": lambda path: __import__("mutagen.mp4", fromlist=["MP4"]).MP4(path),
            ".m4a": lambda path: __import__("mutagen.mp4", fromlist=["MP4"]).MP4(path),
            ".mov": lambda path: __import__("mutagen.mp4", fromlist=["MP4"]).MP4(path),
            ".aac": lambda path: __import__("mutagen.aac", fromlist=["AAC"]).AAC(path),
            ".wma": lambda path: __import__("mutagen.asf", fromlist=["ASF"]).ASF(path),
            ".wmv": lambda path: __import__("mutagen.asf", fromlist=["ASF"]).ASF(path),
        }
        loader = media_loaders.get(suffix)
        media = loader(str(temp_path)) if loader is not None else mutagen.File(str(temp_path))
        if media is None:
            raise SanitizationError(f"Embedded media '{entry_name}' could not be validated")
        if media.tags is not None:
            media.delete()
            media.save()
        return temp_path.read_bytes()
    except (UnsafeFileError, SanitizationError):
        raise
    except Exception as exc:
        raise SanitizationError(f"Failed to sanitize embedded media '{entry_name}'") from exc
    finally:
        temp_path.unlink(missing_ok=True)


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


def _validate_xml_stream(source, description: str) -> None:
    """Stream-parse XML to reject entities, DTDs, and malformed package parts."""

    try:
        for _event, element in SafeET.iterparse(source, events=("end",)):
            element.clear()
    except Exception as exc:
        raise SanitizationError(f"Malformed {description} XML") from exc


def _zip_strip_file(file_path: Path, new_path: Path, mime_type: str | None = None) -> None:
    """Validate and deterministically rewrite an OOXML, ODF, or EPUB package."""

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
            if sum(item.file_size for item in items) > _ZIP_MAX_TOTAL_BYTES:
                raise SanitizationError("ZIP archive uncompressed content is too large")

            is_odf = bool(mime_type and mime_type.startswith(_ODF_MIME_PREFIX))
            is_epub = mime_type == "application/epub+zip"
            is_ooxml = not is_odf and not is_epub
            if is_epub or is_odf:
                items.sort(key=lambda item: _sanitize_zip_entry_name(item.filename) != "mimetype")

            validated_items: list[tuple[zipfile.ZipInfo, str, str, bool]] = []
            registered_names: dict[str, bool] = {}
            for item in items:
                try:
                    _validate_zip_info(item)
                except ValueError as exc:
                    raise SanitizationError(str(exc)) from exc
                safe_name = _sanitize_zip_entry_name(item.filename)
                is_dir = item.is_dir() or safe_name.endswith("/")
                if is_dir and not safe_name.endswith("/"):
                    safe_name = f"{safe_name}/"
                try:
                    _register_zip_name(registered_names, safe_name, is_dir=is_dir)
                except ValueError as exc:
                    raise SanitizationError(str(exc)) from exc
                normalized_name = safe_name.casefold()
                if is_ooxml and _is_ooxml_active_content(safe_name):
                    raise UnsafeFileError(
                        f"OOXML package contains prohibited active content: '{safe_name}'"
                    )
                validated_items.append((item, safe_name, normalized_name, is_dir))

            if is_epub or is_odf:
                mimetype_items = [
                    item
                    for item, _safe_name, normalized_name, is_dir in validated_items
                    if normalized_name == "mimetype" and not is_dir
                ]
                if len(mimetype_items) != 1:
                    raise SanitizationError("Package must contain exactly one mimetype entry")
                with source_archive.open(mimetype_items[0]) as source:
                    declared_mimetype = source.read(256)
                if declared_mimetype != (mime_type or "").encode("ascii"):
                    raise SanitizationError(
                        "Package mimetype entry does not match detected MIME type"
                    )

            epub_manifest: dict[str, str] = {}
            epub_opf_outputs: dict[str, bytes] = {}
            if is_epub:
                for item, safe_name, normalized_name, is_dir in validated_items:
                    if is_dir or Path(safe_name).suffix.casefold() != ".opf":
                        continue
                    with source_archive.open(item) as source:
                        package_data = source.read(_MAX_PACKAGE_XML_BYTES + 1)
                    if len(package_data) > _MAX_PACKAGE_XML_BYTES:
                        raise SanitizationError("EPUB package metadata is too large")
                    stripped_opf, manifest = _strip_epub_package_metadata(
                        package_data,
                        safe_name,
                    )
                    overlap = set(epub_manifest) & set(manifest)
                    if overlap:
                        raise SanitizationError(
                            f"EPUB manifests declare the same resource: {sorted(overlap)[0]}"
                        )
                    epub_manifest.update(manifest)
                    epub_opf_outputs[normalized_name] = stripped_opf

            total_actual = 0
            for item, safe_name, normalized_name, is_dir in validated_items:
                if normalized_name.startswith(("docprops/", "basic/", "scripts/")) or (
                    is_odf and normalized_name == "meta.xml"
                ):
                    continue

                if is_dir:
                    output_archive.writestr(
                        _sanitized_zip_info(
                            safe_name,
                            compress_type=zipfile.ZIP_STORED,
                            is_dir=True,
                        ),
                        b"",
                    )
                    continue

                extension = Path(safe_name).suffix.casefold()
                new_info = _sanitized_zip_info(
                    safe_name,
                    compress_type=(
                        zipfile.ZIP_STORED if safe_name == "mimetype" else zipfile.ZIP_DEFLATED
                    ),
                )

                if is_epub and extension == ".opf":
                    package_data = _read_zip_entry_bounded(source_archive, item, total_actual)
                    total_actual += len(package_data)
                    output_archive.writestr(new_info, epub_opf_outputs[normalized_name])
                    continue

                if is_ooxml and extension == ".rels":
                    relationship_data = _read_zip_entry_bounded(
                        source_archive,
                        item,
                        total_actual,
                    )
                    total_actual += len(relationship_data)
                    if len(relationship_data) > _MAX_PACKAGE_XML_BYTES:
                        raise SanitizationError(
                            f"OOXML relationship file '{safe_name}' is too large"
                        )
                    _reject_external_ooxml_relationships(relationship_data, safe_name)
                    output_archive.writestr(new_info, relationship_data)
                    continue

                declared_media = epub_manifest.get(normalized_name) if is_epub else None
                if declared_media in _EPUB_MARKUP_MEDIA_TYPES:
                    markup_data = _read_zip_entry_bounded(source_archive, item, total_actual)
                    total_actual += len(markup_data)
                    _validate_epub_markup(markup_data, safe_name)
                    output_archive.writestr(new_info, markup_data)
                    continue

                if declared_media in _EPUB_CSS_MEDIA_TYPES:
                    css_data = _read_zip_entry_bounded(source_archive, item, total_actual)
                    total_actual += len(css_data)
                    _validate_epub_css(css_data, safe_name)
                    output_archive.writestr(new_info, css_data)
                    continue

                declared_media_suffix = (
                    _EPUB_MEDIA_TYPES.get(declared_media or "") if is_epub else None
                )
                if (
                    declared_media_suffix is not None
                    or extension in _PACKAGE_MEDIA_EXTENSIONS
                    or extension in _UNSUPPORTED_EMBEDDED_MEDIA_EXTENSIONS
                ):
                    media_data = _read_zip_entry_bounded(source_archive, item, total_actual)
                    total_actual += len(media_data)
                    sanitized_media = _sanitize_embedded_media(
                        media_data,
                        safe_name,
                        suffix_override=declared_media_suffix,
                    )
                    new_info.compress_type = zipfile.ZIP_STORED
                    output_archive.writestr(new_info, sanitized_media)
                    continue

                expected_image_format = (
                    _EPUB_IMAGE_MEDIA_TYPES.get(declared_media or "") if is_epub else None
                )
                if expected_image_format is not None or extension in _PACKAGE_IMAGE_EXTENSIONS:
                    image_data = _read_zip_entry_bounded(source_archive, item, total_actual)
                    total_actual += len(image_data)
                    try:
                        sanitized_image = _sanitize_embedded_image(
                            image_data,
                            safe_name,
                            expected_format=expected_image_format,
                        )
                    except ValueError as exc:
                        raise SanitizationError(str(exc)) from exc
                    new_info.compress_type = zipfile.ZIP_STORED
                    output_archive.writestr(new_info, sanitized_image)
                    continue

                if declared_media == "image/svg+xml" or extension == ".svg":
                    svg_data = _read_zip_entry_bounded(source_archive, item, total_actual)
                    total_actual += len(svg_data)
                    check_svg_safety(svg_data, safe_name)
                    output_archive.writestr(new_info, svg_data)
                    continue

                if extension == ".xml":
                    with source_archive.open(item) as xml_source:
                        if is_odf:
                            _validate_odf_xml_stream(xml_source, f"part '{safe_name}'")
                        else:
                            _validate_xml_stream(xml_source, f"package part '{safe_name}'")

                written = 0
                with (
                    source_archive.open(item) as source,
                    output_archive.open(new_info, "w") as dest,
                ):
                    while chunk := source.read(65536):
                        written += len(chunk)
                        total_actual += len(chunk)
                        if written > _ZIP_MAX_ENTRY_BYTES:
                            raise SanitizationError(
                                f"ZIP entry '{item.filename}' expanded beyond limit"
                            )
                        if total_actual > _ZIP_MAX_TOTAL_BYTES:
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

    suffix = _PACKAGE_SUFFIXES.get(mime_type or "", file_path.suffix or ".zip")
    new_path = _make_temp_path(suffix=suffix)
    try:
        async with image_guard():
            await _shielded_to_thread(_zip_strip_file, file_path, new_path, mime_type)
        return new_path
    except BaseException:
        new_path.unlink(missing_ok=True)
        raise
