"""Office document (OLE2 / OOXML) security and metadata stripping.

Provides:
- _OLE2_AUTO_EXEC: frozenset of dangerous VBA sub/function names
- _scan_vba_for_autoexec: shared VBA macro scanner (raises on auto-exec)
- _check_ole2_macros: VBA macro check accepting bytes or Path
- _strip_ole2_from_path: exiftool-based OLE2 metadata strip (path-based)
- _strip_ooxml_from_path: ZIP-based OOXML metadata strip (removes docProps/)
"""

import asyncio
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.file_security._concurrency import _get_concurrency_guard
from app.core.file_security._zip import (
    _ZIP_MAX_ENTRY_BYTES,
    _ZIP_MAX_TOTAL_BYTES,
    _sanitize_zip_entry_name,
)
from app.core.sandbox import sandboxed_run

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from oletools.olevba import VBA_Parser

# VBA macro sub/function names that execute automatically on document open/close/save
_OLE2_AUTO_EXEC = frozenset(
    {
        "autoopen",
        "document_open",
        "workbook_open",
        "auto_open",
        "autoexec",
        "autoclose",
        "document_close",
        "workbook_close",
        "auto_close",
        "document_beforeclose",
        "workbook_beforeclose",
        "document_beforesave",
        "workbook_beforesave",
        "document_beforeprint",
        "workbook_beforeprint",
        "app_workbookopen",
        "app_workbookbeforeclose",
        "app_workbookbeforesave",
        "app_workbookbeforeprint",
    }
)


async def _run_exiftool(file_path: Path) -> "object":
    """Run ``exiftool -all= -overwrite_original`` on *file_path* in-place."""

    def _run() -> "object":
        return sandboxed_run(
            ["exiftool", "-all=", "-overwrite_original", str(file_path)],
            rw_paths=[file_path.parent],
            timeout=30,
        )

    async with _get_concurrency_guard("subprocess"):
        return await asyncio.to_thread(_run)


def _scan_vba_for_autoexec(vba: "VBA_Parser") -> None:
    """Shared helper: raise ValueError if a VBA_Parser instance contains auto-exec macros.

    Uses oletools' ``analyze_macros()`` which returns ``(type, keyword, description)``
    tuples. Entries with ``type == "AutoExec"`` are the reliable auto-execution
    indicators — more robust than manual line splitting which can be fooled by
    formatting variations.
    """
    if not vba.detect_vba_macros():
        return
    has_macros = False
    for result_type, keyword, _description in vba.analyze_macros():
        has_macros = True
        if result_type == "AutoExec" and keyword.lower() in _OLE2_AUTO_EXEC:
            raise ValueError(
                "Legacy Office file contains auto-executing macros and cannot be uploaded."
            )
    if has_macros:
        logger.warning("OLE2 file contains macros (non-auto-exec) — allowed through")


def _check_ole2_macros(source: "bytes | Path") -> None:
    """Raise ValueError if a legacy Office file contains auto-executing VBA macros.

    Accepts either raw bytes or a file path. Uses oletools' VBA_Parser. Only
    raises on auto-exec macros (AutoOpen, Document_Open, Workbook_Open, etc.).
    Other macros are logged but allowed through. Fails open on any oletools exception.
    """
    from oletools.olevba import VBA_Parser

    try:
        if isinstance(source, bytes):
            vba = VBA_Parser("file", data=source)
        else:
            vba = VBA_Parser(str(source))
        _scan_vba_for_autoexec(vba)
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("OLE2 structure malformed, failing closed: %s", exc)
        raise ValueError("File appears malformed or corrupted and cannot be validated for safety.")


async def _strip_ole2_from_path(file_path: Path) -> Path:
    """Remove metadata from legacy Office files on disk using exiftool."""
    import shutil

    # 1. Block auto-exec macros (must propagate ValueError)
    await asyncio.to_thread(_check_ole2_macros, file_path)

    # 2. Copy so exiftool's in-place edit never touches the original.
    new_path = Path(tempfile.NamedTemporaryFile(delete=False).name)
    await asyncio.to_thread(shutil.copyfile, file_path, new_path)

    try:
        result = await _run_exiftool(new_path)
        if result.returncode != 0:  # type: ignore
            logger.warning(
                "exiftool OLE2 metadata strip path failed (rc=%d): %s",
                result.returncode,  # type: ignore
                result.stderr[:500],  # type: ignore
            )
            new_path.unlink(missing_ok=True)
            return file_path
        return new_path
    except Exception:
        new_path.unlink(missing_ok=True)
        raise


async def _strip_ooxml_from_path(file_path: Path) -> Path:
    """Remove metadata from OOXML files (.docx, .xlsx, .pptx) using zipfile.

    This removes the docProps/ directory which contains core.xml (author,
    created date) and app.xml (application name, version).
    """
    new_path = Path(tempfile.NamedTemporaryFile(delete=False).name)

    try:

        def _zip_strip() -> None:
            with (
                zipfile.ZipFile(file_path, "r") as zin,
                zipfile.ZipFile(new_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout,
            ):
                total_declared = sum(i.file_size for i in zin.infolist())
                if total_declared > _ZIP_MAX_TOTAL_BYTES:
                    raise ValueError("ZIP archive uncompressed content is too large")

                total_actual_written = 0
                for item in zin.infolist():
                    if item.file_size > _ZIP_MAX_ENTRY_BYTES:
                        raise ValueError(f"ZIP entry '{item.filename}' is too large")

                    # Strip docProps/ (OOXML metadata: core.xml, app.xml, custom.xml)
                    # Strip Basic/ and Scripts/ (ODF macro/script containers)
                    if item.filename.startswith(("docProps/", "Basic/", "Scripts/")):
                        continue

                    safe_name = _sanitize_zip_entry_name(item.filename)
                    new_info = zipfile.ZipInfo(filename=safe_name, date_time=item.date_time)
                    new_info.compress_type = zipfile.ZIP_DEFLATED

                    with zin.open(item) as src, zout.open(new_info, "w") as dest:
                        written = 0
                        while True:
                            chunk = src.read(65536)
                            if not chunk:
                                break
                            written += len(chunk)
                            total_actual_written += len(chunk)

                            if written > _ZIP_MAX_ENTRY_BYTES:
                                raise ValueError(
                                    f"ZIP entry '{item.filename}' expanded beyond limit"
                                )
                            if total_actual_written > _ZIP_MAX_TOTAL_BYTES:
                                raise ValueError(
                                    "ZIP archive actual uncompressed content exceeds total limit"
                                )

                            dest.write(chunk)

        await asyncio.to_thread(_zip_strip)
        return new_path
    except ValueError:
        new_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        logger.warning("OOXML metadata strip failed for %s: %s", file_path, e)
        new_path.unlink(missing_ok=True)
        return file_path
