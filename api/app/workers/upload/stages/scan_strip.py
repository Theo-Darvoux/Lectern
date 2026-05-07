import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from app.core.exceptions import BadRequestError
from app.core.file_security import check_pdf_safety, strip_metadata_file
from app.core.metrics import upload_scan_duration
from app.core.processing import ProcessingFile
from app.core.scanner import MalwareScanner
from app.schemas.material import UploadStatus
from app.workers.upload.context import WorkerContext
from app.workers.upload.exceptions import MalwareError, UploadError
from app.workers.upload.utils import parallel_tasks

logger = logging.getLogger("wikint")


async def run_scan_and_strip(
    ctx: WorkerContext,
    pf: ProcessingFile,
    tmp_path: Path,
    original_filename: str,
    original_sha256: str,
    mime_type: str,
    mime_category: str,
    upload_id: str,
    tracer: Any,
) -> None:
    from app.workers.upload.pipeline import _get_fallback_scanner

    scanner: MalwareScanner = ctx.scanner or _get_fallback_scanner()
    owns_scanner = ctx.scanner is None
    scan_start = time.monotonic()

    scan_copy = tmp_path.with_suffix(".scan")
    await asyncio.to_thread(shutil.copyfile, tmp_path, scan_copy)

    async def _run_scan() -> None:
        try:
            with tracer.start_as_current_span("upload.scan") as span:
                span.set_attribute("upload.id", upload_id)
                span.set_attribute("upload.mime_category", mime_category)
                await asyncio.wait_for(
                    scanner.scan_file_path(
                        scan_copy,
                        original_filename,
                        bazaar_hash=original_sha256,
                    ),
                    timeout=120.0,
                )
        finally:
            scan_copy.unlink(missing_ok=True)

    async def _run_strip() -> Path:
        with tracer.start_as_current_span("upload.strip_metadata"):
            return await asyncio.wait_for(
                strip_metadata_file(tmp_path, mime_type),
                timeout=60.0,
            )

    try:
        results = await parallel_tasks(_run_scan(), _run_strip())
        scan_res, strip_res = results[0], results[1]
    finally:
        if owns_scanner:
            await scanner.close()

    upload_scan_duration.labels(mime_category=mime_category).observe(time.monotonic() - scan_start)

    # Error handling
    if isinstance(scan_res, TimeoutError):
        raise UploadError(UploadStatus.FAILED, "Malware scan timed out")
    if isinstance(scan_res, BadRequestError):
        detail = str(scan_res.detail) if hasattr(scan_res, "detail") else str(scan_res)
        raise MalwareError(detail)
    if isinstance(scan_res, BaseException):
        raise scan_res

    if isinstance(strip_res, TimeoutError):
        raise UploadError(UploadStatus.FAILED, "Metadata stripping timed out")
    if isinstance(strip_res, ValueError):
        raise MalwareError(str(strip_res))
    if isinstance(strip_res, BaseException):
        logger.warning("Strip failed for %s (ignored): %s", upload_id, strip_res)
    elif isinstance(strip_res, Path) and strip_res != tmp_path:
        pf.replace_with(strip_res)


async def run_strip_only(
    pf: ProcessingFile,
    tmp_path: Path,
    mime_type: str,
    upload_id: str,
    tracer: Any,
) -> None:
    with tracer.start_as_current_span("upload.strip_metadata"):
        try:
            clean_path = await asyncio.wait_for(
                strip_metadata_file(tmp_path, mime_type),
                timeout=60.0,
            )
            if clean_path != tmp_path:
                pf.replace_with(clean_path)
        except TimeoutError:
            raise UploadError(UploadStatus.FAILED, "Metadata stripping timed out")
        except ValueError as exc:
            raise MalwareError(str(exc))


async def run_post_strip_pdf_check(
    pf: ProcessingFile,
    mime_type: str,
) -> None:
    if mime_type != "application/pdf":
        return

    try:
        await asyncio.to_thread(check_pdf_safety, pf.path)
    except ValueError as exc:
        raise MalwareError(str(exc))
