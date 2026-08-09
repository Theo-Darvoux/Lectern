"""Command entrypoint executed only inside the hostile-file sandbox."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


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
            raise ValueError("Failed to sanitize legacy Office document metadata")
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
    return {"match": matches[0].rule if matches else None}


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
        raise ValueError("thumbnail requires input, output, width, height, quality and flatten flag")
    from PIL import Image, ImageOps, ImageStat

    from app.core.security.file_security._image import _validate_image_size

    input_path, output_path = Path(arguments[0]), Path(arguments[1])
    size = (int(arguments[2]), int(arguments[3]))
    quality = int(arguments[4])
    flatten = arguments[5] == "1"
    with Image.open(input_path) as base_img:
        _validate_image_size(base_img)
        if getattr(base_img, "n_frames", 1) != 1 or getattr(base_img, "is_animated", False):
            raise ValueError("Animated thumbnail sources are not supported")
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
                    gray = rendered.convert("L")
                    try:
                        stat = ImageStat.Stat(gray)
                        blank = stat.mean[0] >= 252.0 and stat.stddev[0] <= 4.0
                    finally:
                        gray.close()
                finally:
                    background.close()
                    alpha.close()
                    if rgba is not oriented:
                        rgba.close()
            else:
                rendered.save(output_path, "WEBP", quality=quality)
                gray = rendered.convert("L")
                try:
                    stat = ImageStat.Stat(gray)
                    blank = stat.mean[0] >= 252.0 and stat.stddev[0] <= 4.0
                finally:
                    gray.close()
        finally:
            if oriented is not base_img:
                oriented.close()
    return {"blank": blank, "size": output_path.stat().st_size}


def _extract_office_thumbnail(arguments: list[str]) -> dict[str, object]:
    if len(arguments) != 5:
        raise ValueError("office-thumbnail requires input, output, width, height and quality")
    import zipfile

    input_path, output_path = Path(arguments[0]), Path(arguments[1])
    with zipfile.ZipFile(input_path, "r") as archive:
        candidates = [
            entry
            for entry in archive.infolist()
            if entry.filename.lower().endswith((".png", ".jpg", ".jpeg"))
        ]
        if not candidates:
            return {"produced": False}
        largest = max(candidates, key=lambda entry: entry.file_size)
        if largest.file_size > 20 * 1024 * 1024:
            raise ValueError("Embedded thumbnail candidate exceeds byte limit")
        with archive.open(largest) as source:
            data = source.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("Embedded thumbnail candidate expanded beyond byte limit")
    from io import BytesIO

    from PIL import Image

    from app.core.security.file_security._image import _validate_image_size

    with BytesIO(data) as encoded, Image.open(encoded) as image:
        _validate_image_size(image)
        if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
            raise ValueError("Animated embedded thumbnails are not supported")
        image.load()
        image.thumbnail((int(arguments[2]), int(arguments[3])), Image.Resampling.LANCZOS)
        image.save(output_path, "WEBP", quality=int(arguments[4]))
    return {"produced": True}


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
        elif operation == "extract-zip":
            payload = _extract_zip(arguments)
        else:
            raise ValueError(f"Unsupported parser operation: {operation!r}")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
