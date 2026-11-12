"""Command entrypoint executed only inside the hostile-file sandbox."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.security.file_security.errors import UnreadableFileError

if TYPE_CHECKING:
    from PIL import Image


def is_blank_image(image: Image.Image) -> bool:
    """Whether a rendered page is near-uniform white, with nothing worth showing."""
    from PIL import ImageStat

    gray = image.convert("L")
    try:
        stat = ImageStat.Stat(gray)
        return stat.mean[0] >= 252.0 and stat.stddev[0] <= 4.0
    finally:
        gray.close()


def _inspect(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 4:
        raise ValueError("inspect requires path, filename, declared MIME and archive flag")

    file_path = Path(arguments[0])
    filename = arguments[1]
    declared_mime = arguments[2]
    inspect_archive = arguments[3] == "1"

    from app.core.media.mimetypes import MimeRegistry, guess_mime_from_file_path
    from app.core.security.file_security import check_svg_safety, get_uncompressed_size
    from app.core.security.polyglot import check_polyglot

    uncompressed_size = get_uncompressed_size(file_path) if inspect_archive else None
    detected_mime = guess_mime_from_file_path(file_path)
    actual_mime = MimeRegistry.get_authoritative_mime(filename, detected_mime)
    normalized_declared = MimeRegistry.normalize_mime(declared_mime)
    if actual_mime == "application/octet-stream":
        actual_mime = normalized_declared

    check_polyglot(file_path, actual_mime)
    if actual_mime == "image/svg+xml":
        check_svg_safety(file_path.read_bytes(), filename)

    return {
        "actual_mime": actual_mime,
        "uncompressed_size": uncompressed_size,
        "parser_pid": os.getpid(),
        "parser_uid": os.getuid(),
    }


def _sanitize(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 4:
        raise ValueError("sanitize requires path, MIME, work directory and output path")
    file_path = Path(arguments[0])
    mime_type = arguments[1]
    work_dir = Path(arguments[2])
    output_path = Path(arguments[3])

    from app.config import settings
    from app.core.media.mimetypes import OLE2_MIME_TYPES
    from app.core.security.file_security.strip import strip_metadata_file

    settings.processing_root = str(work_dir)
    if mime_type in OLE2_MIME_TYPES:
        from app.core.security.file_security._office import _check_ole2_macros

        _check_ole2_macros(file_path)
        sanitized = work_dir / f"document{file_path.suffix or '.doc'}"
        shutil.copyfile(file_path, sanitized)
        exiftool = shutil.which("exiftool")
        if exiftool is None:
            raise RuntimeError("exiftool is required for legacy Office sanitization")
        completed = subprocess.run(
            [exiftool, "-all=", "-overwrite_original", str(sanitized)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise UnreadableFileError("Failed to sanitize legacy Office document metadata")
    else:
        sanitized = asyncio.run(strip_metadata_file(file_path, mime_type))
    if sanitized == file_path or not sanitized.is_file():
        raise ValueError("High-risk sanitizer did not produce a distinct output")
    shutil.copyfile(sanitized, output_path)
    return {"size": output_path.stat().st_size}


def _check_pdf(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 1:
        raise ValueError("pdf-check requires one path")
    from app.core.security.file_security._pdf import check_pdf_safety

    check_pdf_safety(Path(arguments[0]))
    return {"safe": True}


def _scan_yara(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 3:
        raise ValueError("yara requires input path, compiled rules and timeout")
    import yara

    rules = yara.load(str(Path(arguments[1])))
    matches = rules.match(filepath=str(Path(arguments[0])), timeout=int(arguments[2]))
    if not matches:
        return {"match": None}

    match = matches[0]
    description = match.meta.get("description")
    reason = (
        f"{description} (YARA rule: {match.rule})"
        if isinstance(description, str) and description.strip()
        else f"Matched local malware rule: {match.rule}"
    )
    return {"match": reason}


def _render_avatar(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 4:
        raise ValueError("avatar requires input path, output path, size and quality")
    from app.core.media.avatar_processor import process_avatar

    output_path = Path(arguments[1])
    rendered = process_avatar(
        Path(arguments[0]),
        size=int(arguments[2]),
        quality=int(arguments[3]),
    )
    output_path.write_bytes(rendered)
    return {"size": len(rendered)}


def _render_thumbnail(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 6:
        raise ValueError(
            "thumbnail requires input, output, width, height, quality and flatten flag"
        )
    from PIL import Image, ImageOps

    from app.core.security.file_security._image import _validate_image_size

    input_path, output_path = Path(arguments[0]), Path(arguments[1])
    size = (int(arguments[2]), int(arguments[3]))
    quality = int(arguments[4])
    flatten = arguments[5] == "1"
    with Image.open(input_path) as base_img:
        _validate_image_size(base_img)
        # Animated GIF/WebP/APNG: the thumbnail is the first frame.
        base_img.seek(0)
        base_img.load()
        oriented = ImageOps.exif_transpose(base_img)
        try:
            oriented.thumbnail(size, Image.Resampling.LANCZOS)
            rendered = oriented
            if flatten and oriented.mode in ("RGBA", "LA", "PA"):
                rgba = oriented.convert("RGBA") if oriented.mode != "RGBA" else oriented
                alpha = rgba.getchannel("A")
                background = Image.new("RGB", rgba.size, "white")
                try:
                    background.paste(rgba, mask=alpha)
                    rendered = background
                    rendered.save(output_path, "WEBP", quality=quality)
                    blank = is_blank_image(rendered)
                finally:
                    background.close()
                    alpha.close()
                    if rgba is not oriented:
                        rgba.close()
            else:
                rendered.save(output_path, "WEBP", quality=quality)
                blank = is_blank_image(rendered)
        finally:
            if oriented is not base_img:
                oriented.close()
    return {"blank": blank, "size": output_path.stat().st_size}


_EMBEDDED_IMAGE_MAX_BYTES = 20 * 1024 * 1024
_EPUB_XML_MAX_BYTES = 5 * 1024 * 1024
_RASTER_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _read_zip_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, limit: int) -> bytes:
    """Read one archive member, refusing declared or actual sizes above ``limit``."""
    if member.file_size > limit:
        raise ValueError(f"Archive member {member.filename!r} exceeds byte limit")
    with archive.open(member) as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Archive member {member.filename!r} expanded beyond byte limit")
    return data


def _render_zip_image(
    archive: zipfile.ZipFile, member: zipfile.ZipInfo, arguments: list[str]
) -> dict[str, object]:
    """Render one image stored in an archive to the WebP output path."""
    from io import BytesIO

    from PIL import Image

    from app.core.security.file_security._image import _validate_image_size

    data = _read_zip_member(archive, member, _EMBEDDED_IMAGE_MAX_BYTES)
    with BytesIO(data) as encoded, Image.open(encoded) as image:
        _validate_image_size(image)
        image.seek(0)
        image.load()
        rendered = image.convert("RGB") if image.mode not in ("RGB", "RGBA") else image
        rendered.thumbnail((int(arguments[2]), int(arguments[3])), Image.Resampling.LANCZOS)
        rendered.save(Path(arguments[1]), "WEBP", quality=int(arguments[4]))
    return {"produced": True}


def _extract_office_thumbnail(arguments: list[str]) -> dict[str, object]:
    """Render the largest raster image embedded in an OOXML/ODF container."""
    if len(arguments) != 5:
        raise ValueError("office-thumbnail requires input, output, width, height and quality")
    with zipfile.ZipFile(arguments[0], "r") as archive:
        candidates = [
            entry
            for entry in archive.infolist()
            if entry.filename.lower().endswith((".png", ".jpg", ".jpeg"))
        ]
        if not candidates:
            return {"produced": False}
        largest = max(candidates, key=lambda entry: entry.file_size)
        return _render_zip_image(archive, largest, arguments)


def _epub_cover_member(archive: zipfile.ZipFile) -> zipfile.ZipInfo | None:
    """Find the cover image an EPUB package declares, if any.

    Order: EPUB 3 ``properties="cover-image"``, EPUB 2 ``<meta name="cover">``,
    then a manifest image whose id or href names a cover.
    """
    import posixpath
    from urllib.parse import unquote

    from defusedxml import ElementTree

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    names = set(archive.namelist())
    if "META-INF/container.xml" not in names:
        return None
    container = ElementTree.fromstring(
        _read_zip_member(archive, archive.getinfo("META-INF/container.xml"), _EPUB_XML_MAX_BYTES)
    )
    opf_path = next(
        (
            element.get("full-path", "")
            for element in container.iter()
            if local(element.tag) == "rootfile"
        ),
        "",
    )
    if opf_path not in names:
        return None
    package = ElementTree.fromstring(
        _read_zip_member(archive, archive.getinfo(opf_path), _EPUB_XML_MAX_BYTES)
    )

    items: list[dict[str, str]] = []
    cover_id = ""
    for element in package.iter():
        tag = local(element.tag)
        if tag == "item":
            items.append({key: value or "" for key, value in element.attrib.items()})
        elif tag == "meta" and element.get("name") == "cover":
            cover_id = element.get("content", "")

    images = [
        item
        for item in items
        if item.get("media-type", "").startswith("image/")
        or item.get("href", "").lower().endswith(_RASTER_SUFFIXES)
    ]
    chosen = (
        next((i for i in images if "cover-image" in i.get("properties", "").split()), None)
        or next((i for i in images if cover_id and i.get("id") == cover_id), None)
        or next(
            (
                i
                for i in images
                if "cover" in i.get("id", "").lower() or "cover" in i.get("href", "").lower()
            ),
            None,
        )
    )
    if chosen is None or not chosen.get("href"):
        return None
    member_path = posixpath.normpath(
        posixpath.join(posixpath.dirname(opf_path), unquote(chosen["href"]))
    )
    if member_path not in names or not member_path.lower().endswith(_RASTER_SUFFIXES):
        return None
    return archive.getinfo(member_path)


def _extract_epub_cover(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 5:
        raise ValueError("epub-cover requires input, output, width, height and quality")
    with zipfile.ZipFile(arguments[0], "r") as archive:
        member = _epub_cover_member(archive)
        if member is None:
            return {"produced": False}
        return _render_zip_image(archive, member, arguments)


def _extract_zip(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 3:
        raise ValueError("extract-zip requires input path, extraction root and member limit")
    from app.core.common.exceptions import BadRequestError
    from app.routers.upload.batch_zip import _extract_zip_sync

    try:
        entries, skipped = _extract_zip_sync(arguments[0], arguments[1], int(arguments[2]))
    except BadRequestError as exc:
        return {"error": {"detail": exc.detail, "code": exc.code}}
    return {
        "entries": [
            {
                "tmp_name": entry.tmp_path.name,
                "filename": entry.filename,
                "relative_path": entry.relative_path,
                "size": entry.size,
            }
            for entry in entries
        ],
        "skipped": skipped,
    }


EXIT_UNREADABLE_FILE = 3


def main() -> int:
    try:
        operation, *arguments = sys.argv[1:]
        if operation == "inspect":
            payload = _inspect(arguments)
        elif operation == "sanitize":
            payload = _sanitize(arguments)
        elif operation == "pdf-check":
            payload = _check_pdf(arguments)
        elif operation == "yara":
            payload = _scan_yara(arguments)
        elif operation == "avatar":
            payload = _render_avatar(arguments)
        elif operation == "thumbnail":
            payload = _render_thumbnail(arguments)
        elif operation == "office-thumbnail":
            payload = _extract_office_thumbnail(arguments)
        elif operation == "epub-cover":
            payload = _extract_epub_cover(arguments)
        elif operation == "extract-zip":
            payload = _extract_zip(arguments)
        else:
            raise ValueError(f"Unsupported parser operation: {operation!r}")
    except UnreadableFileError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_UNREADABLE_FILE
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
