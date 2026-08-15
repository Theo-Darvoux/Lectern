"""Deep interface for parsing hostile uploads outside the queue worker process."""

from __future__ import annotations

import asyncio
import json
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.common.upload_limits import maximum_upload_size_bytes
from app.core.media.mimetypes import OLE2_MIME_TYPES, ZIP_MIME_TYPES
from app.core.security.file_security._concurrency import image_guard, run_managed_subprocess
from app.core.security.processing_paths import make_processing_temp_dir, make_processing_temp_path


@dataclass(frozen=True)
class UploadInspection:
    actual_mime: str
    uncompressed_size: int | None
    parser_pid: int
    parser_uid: int


@dataclass(frozen=True)
class IsolatedZipEntry:
    tmp_path: Path
    filename: str
    relative_path: str
    size: int


def requires_isolated_sanitization(mime_type: str) -> bool:
    return (
        (mime_type.startswith("image/") and mime_type != "image/svg+xml")
        or mime_type == "application/pdf"
        or mime_type in OLE2_MIME_TYPES
        or mime_type in ZIP_MIME_TYPES
    )


async def inspect_upload(
    file_path: Path,
    *,
    filename: str,
    declared_mime: str,
    inspect_archive: bool,
    timeout: int = 60,
) -> UploadInspection:
    """Inspect one hostile upload in a resource-limited network namespace."""
    result = await run_managed_subprocess(
        [
            str(Path(sys.executable).resolve()),
            "-m",
            "app.core.security.parser_child",
            "inspect",
            str(file_path),
            filename,
            declared_mime,
            "1" if inspect_archive else "0",
        ],
        ro_paths=[file_path],
        timeout=timeout,
        python_runtime=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[:2000].strip()
        raise ValueError(detail or "Isolated upload inspection failed")
    try:
        payload = json.loads(result.stdout)
        return UploadInspection(
            actual_mime=str(payload["actual_mime"]),
            uncompressed_size=(
                int(payload["uncompressed_size"])
                if payload["uncompressed_size"] is not None
                else None
            ),
            parser_pid=int(payload["parser_pid"]),
            parser_uid=int(payload["parser_uid"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Isolated parser returned an invalid response") from exc


async def sanitize_upload(
    file_path: Path,
    *,
    mime_type: str,
    timeout: int = 60,
) -> Path:
    """Sanitize hostile structured bytes and return a parent-owned output file."""
    work_dir = make_processing_temp_dir(prefix="parser-sanitize-")
    output_path = make_processing_temp_path(suffix=file_path.suffix, prefix="sanitized-")
    try:
        async with image_guard():
            result = await run_managed_subprocess(
                [
                    str(Path(sys.executable).resolve()),
                    "-m",
                    "app.core.security.parser_child",
                    "sanitize",
                    str(file_path),
                    mime_type,
                    str(work_dir),
                    str(output_path),
                ],
                ro_paths=[file_path],
                rw_paths=[work_dir, output_path],
                timeout=timeout,
                python_runtime=True,
                check=False,
            )
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace")[:2000].strip()
            raise ValueError(detail or "Isolated upload sanitization failed")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise ValueError("Isolated sanitizer did not produce an output file")
        return output_path
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def check_pdf_safety_isolated(file_path: Path, *, timeout: int = 30) -> None:
    result = await run_managed_subprocess(
        [
            str(Path(sys.executable).resolve()),
            "-m",
            "app.core.security.parser_child",
            "pdf-check",
            str(file_path),
        ],
        ro_paths=[file_path],
        timeout=timeout,
        python_runtime=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[:2000].strip()
        raise ValueError(detail or "Isolated PDF safety validation failed")


async def process_avatar_isolated(
    file_path: Path,
    *,
    size: int = 256,
    quality: int = 60,
    timeout: int = 30,
) -> bytes:
    """Render a sanitized upload into WebP outside the API process."""
    output_path = make_processing_temp_path(suffix=".webp", prefix="avatar-")
    try:
        async with image_guard():
            result = await run_managed_subprocess(
                [
                    str(Path(sys.executable).resolve()),
                    "-m",
                    "app.core.security.parser_child",
                    "avatar",
                    str(file_path),
                    str(output_path),
                    str(size),
                    str(quality),
                ],
                ro_paths=[file_path],
                rw_paths=[output_path],
                timeout=timeout,
                python_runtime=True,
                check=False,
            )
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace")[:2000].strip()
            raise ValueError(detail or "Isolated avatar rendering failed")
        data = await asyncio.to_thread(output_path.read_bytes)
        if not data.startswith(b"RIFF") or b"WEBP" not in data[:16]:
            raise ValueError("Isolated avatar renderer returned an invalid WebP file")
        return data
    finally:
        output_path.unlink(missing_ok=True)


async def render_thumbnail_isolated(
    input_path: Path,
    output_path: Path,
    *,
    size: tuple[int, int],
    quality: int,
    flatten_alpha: bool = False,
    timeout: int = 30,
) -> bool:
    """Render one raster source and report whether its result is nearly blank."""
    output_path.touch(mode=0o600, exist_ok=True)
    output_path.chmod(0o600)
    async with image_guard():
        result = await run_managed_subprocess(
            [
                str(Path(sys.executable).resolve()),
                "-m",
                "app.core.security.parser_child",
                "thumbnail",
                str(input_path),
                str(output_path),
                str(size[0]),
                str(size[1]),
                str(quality),
                "1" if flatten_alpha else "0",
            ],
            ro_paths=[input_path],
            rw_paths=[output_path],
            timeout=timeout,
            python_runtime=True,
            check=False,
        )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[:2000].strip()
        raise ValueError(detail or "Isolated thumbnail rendering failed")
    try:
        payload = json.loads(result.stdout)
        blank = payload["blank"]
        if not isinstance(blank, bool) or int(payload["size"]) <= 0:
            raise TypeError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Isolated thumbnail renderer returned an invalid response") from exc
    header = output_path.read_bytes()[:16]
    if not header.startswith(b"RIFF") or b"WEBP" not in header:
        raise ValueError("Isolated thumbnail renderer returned an invalid WebP file")
    return blank


async def extract_office_thumbnail_isolated(
    input_path: Path,
    output_path: Path,
    *,
    size: tuple[int, int],
    quality: int,
    timeout: int = 30,
) -> bool:
    """Extract and render an Office fallback image in the parser sandbox."""
    output_path.touch(mode=0o600, exist_ok=True)
    output_path.chmod(0o600)
    async with image_guard():
        result = await run_managed_subprocess(
            [
                str(Path(sys.executable).resolve()),
                "-m",
                "app.core.security.parser_child",
                "office-thumbnail",
                str(input_path),
                str(output_path),
                str(size[0]),
                str(size[1]),
                str(quality),
            ],
            ro_paths=[input_path],
            rw_paths=[output_path],
            timeout=timeout,
            python_runtime=True,
            check=False,
        )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[:2000].strip()
        raise ValueError(detail or "Isolated Office thumbnail fallback failed")
    try:
        produced = json.loads(result.stdout)["produced"]
        if not isinstance(produced, bool):
            raise TypeError
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Isolated Office thumbnail returned an invalid response") from exc
    if produced:
        header = output_path.read_bytes()[:16]
        if not header.startswith(b"RIFF") or b"WEBP" not in header:
            raise ValueError("Isolated Office thumbnail returned an invalid WebP file")
    else:
        output_path.unlink(missing_ok=True)
    return produced


async def scan_yara_isolated(
    file_path: Path,
    *,
    compiled_rules_path: Path,
    timeout: int,
) -> str | None:
    result = await run_managed_subprocess(
        [
            str(Path(sys.executable).resolve()),
            "-m",
            "app.core.security.parser_child",
            "yara",
            str(file_path),
            str(compiled_rules_path),
            str(timeout),
        ],
        ro_paths=[file_path, compiled_rules_path],
        timeout=timeout + 5,
        python_runtime=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[:2000].strip()
        raise RuntimeError(detail or "Isolated YARA scan failed")
    try:
        payload = json.loads(result.stdout)
        match = payload["match"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Isolated YARA scanner returned an invalid response") from exc
    if match is not None and not isinstance(match, str):
        raise RuntimeError("Isolated YARA scanner returned an invalid match")
    return match


async def extract_zip_isolated(
    zip_path: Path,
    *,
    extraction_root: Path,
    max_members: int,
    timeout: int = 120,
) -> tuple[list[IsolatedZipEntry], list[str]]:
    """Extract an untrusted batch archive in a disposable parser process."""
    from app.core.common.exceptions import BadRequestError

    result = await run_managed_subprocess(
        [
            str(Path(sys.executable).resolve()),
            "-m",
            "app.core.security.parser_child",
            "extract-zip",
            str(zip_path),
            str(extraction_root),
            str(max_members),
        ],
        ro_paths=[zip_path],
        rw_paths=[extraction_root],
        timeout=timeout,
        python_runtime=True,
        file_size_limit_bytes=maximum_upload_size_bytes(),
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[:2000].strip()
        raise RuntimeError(detail or "Isolated ZIP extraction failed")

    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise TypeError
        error = payload.get("error")
        if error is not None:
            if not isinstance(error, dict):
                raise TypeError
            raise BadRequestError(str(error["detail"]), code=str(error["code"]))
        raw_entries = payload["entries"]
        raw_skipped = payload["skipped"]
        if not isinstance(raw_entries, list) or not isinstance(raw_skipped, list):
            raise TypeError
        if len(raw_entries) > max_members or not all(isinstance(item, str) for item in raw_skipped):
            raise TypeError

        entries: list[IsolatedZipEntry] = []
        seen_paths: set[Path] = set()
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                raise TypeError
            tmp_name = raw_entry["tmp_name"]
            if not isinstance(tmp_name, str) or not tmp_name.startswith("entry_"):
                raise TypeError
            if not tmp_name.removeprefix("entry_").isdigit():
                raise TypeError
            tmp_path = extraction_root / tmp_name
            if tmp_path in seen_paths or tmp_path.parent != extraction_root:
                raise TypeError
            seen_paths.add(tmp_path)
            file_stat = tmp_path.lstat()
            size = int(raw_entry["size"])
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != size or size < 0:
                raise TypeError
            filename = raw_entry["filename"]
            relative_path = raw_entry["relative_path"]
            if not isinstance(filename, str) or not isinstance(relative_path, str):
                raise TypeError
            entries.append(
                IsolatedZipEntry(
                    tmp_path=tmp_path,
                    filename=filename,
                    relative_path=relative_path,
                    size=size,
                )
            )
        return entries, raw_skipped
    except BadRequestError:
        raise
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Isolated ZIP extractor returned an invalid response") from exc
