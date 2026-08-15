import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from app.core.common.exceptions import BadRequestError
from app.core.events.processing import ProcessingFile
from app.core.observability.metrics import upload_scan_duration
from app.core.security.async_utils import shielded_await, shielded_to_thread
from app.core.security.file_security import strip_metadata_file
from app.core.security.isolated_parser import (
    check_pdf_safety_isolated,
    requires_isolated_sanitization,
    sanitize_upload,
)
from app.core.security.scanner import MalwareScanner
from app.schemas.material import UploadStatus
from app.workers.upload.context import WorkerContext
from app.workers.upload.exceptions import MalwareError, UploadError
from app.workers.upload.utils import parallel_tasks

logger = logging.getLogger(__name__)


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
    try:
        await shielded_to_thread(shutil.copyfile, tmp_path, scan_copy)
    except BaseException:
        scan_copy.unlink(missing_ok=True)
        if owns_scanner:
            await shielded_await(scanner.close(), description="scanner close")
        raise

    async def _run_scan() -> None:
        try:
            with tracer.start_as_current_span("upload.scan") as span:
                span.set_attribute("upload.id", upload_id)
                span.set_attribute("upload.mime_category", mime_category)
                await asyncio.wait_for(
                    scanner.scan_file_path(
                        scan_copy,
                        original_filename,
                    ),
                    timeout=120.0,
                )
        finally:
            scan_copy.unlink(missing_ok=True)

    async def _run_strip() -> Path:
        with tracer.start_as_current_span("upload.strip_metadata"):
            return await asyncio.wait_for(
                (
                    sanitize_upload(tmp_path, mime_type=mime_type)
                    if requires_isolated_sanitization(mime_type)
                    else strip_metadata_file(tmp_path, mime_type)
                ),
                timeout=60.0,
            )

    try:
        results = await parallel_tasks(_run_scan(), _run_strip())
        scan_res, strip_res = results[0], results[1]
    finally:
        if owns_scanner:
            await shielded_await(scanner.close(), description="scanner close")

    upload_scan_duration.labels(mime_category=mime_category).observe(time.monotonic() - scan_start)

    try:
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
            raise strip_res
        if isinstance(strip_res, Path) and strip_res != tmp_path:
            await pf.replace_with(strip_res)
    except BaseException:
        if isinstance(strip_res, Path) and strip_res != tmp_path and pf.path != strip_res:
            try:
                strip_res.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Failed to clean up leaked strip_res path %s: %s", strip_res, e)
        raise


async def run_strip_only(
    pf: ProcessingFile,
    tmp_path: Path,
    mime_type: str,
    upload_id: str,
    tracer: Any,
) -> None:
    clean_path = None
    with tracer.start_as_current_span("upload.strip_metadata"):
        try:
            clean_path = await asyncio.wait_for(
                (
                    sanitize_upload(tmp_path, mime_type=mime_type)
                    if requires_isolated_sanitization(mime_type)
                    else strip_metadata_file(tmp_path, mime_type)
                ),
                timeout=60.0,
            )
            if clean_path != tmp_path:
                await pf.replace_with(clean_path)
        except TimeoutError:
            if clean_path is not None and clean_path != tmp_path and pf.path != clean_path:
                clean_path.unlink(missing_ok=True)
            raise UploadError(UploadStatus.FAILED, "Metadata stripping timed out")
        except BaseException as exc:
            if clean_path is not None and clean_path != tmp_path and pf.path != clean_path:
                clean_path.unlink(missing_ok=True)
            if isinstance(exc, ValueError):
                raise MalwareError(str(exc))
            raise


async def run_post_strip_pdf_check(
    pf: ProcessingFile,
    mime_type: str,
) -> None:
    if mime_type != "application/pdf":
        return

    try:
        await check_pdf_safety_isolated(pf.path)
    except ValueError as exc:
        raise MalwareError(str(exc))
