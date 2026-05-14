import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.config import settings
from app.core.metrics import mime_category as _mime_cat
from app.core.metrics import (
    upload_file_size,
    upload_pipeline_duration,
    upload_pipeline_total,
)
from app.core.processing import ProcessingFile
from app.core.scanner import MalwareScanner
from app.core.storage import delete_object
from app.core.telemetry import get_tracer
from app.schemas.material import UploadStatus
from app.workers.upload.cache_repo import UploadCacheRepository
from app.workers.upload.constants import (
    _CANCEL_KEY_PREFIX,
    _MAX_ARQ_RETRIES,
    _STAGE_TOTAL,
    _STAGES,
    _overall,
)
from app.workers.upload.context import WorkerContext
from app.workers.upload.exceptions import UploadError
from app.workers.upload.repository import UploadWorkerRepository
from app.workers.upload.stages.download import run_download_and_validate
from app.workers.upload.stages.finalize import FinalizeInput, run_finalize_storage
from app.workers.upload.stages.scan_strip import (
    run_post_strip_pdf_check,
    run_scan_and_strip,
    run_strip_only,
)

logger = logging.getLogger("wikint")


def _get_fallback_scanner() -> MalwareScanner:
    """Create a one-shot scanner for contexts without a pooled instance (e.g. tests)."""
    scanner = MalwareScanner()
    scanner.initialize()
    return scanner


def _get_stage_index(stage_name_or_label: str) -> int:
    for idx, (_, label, name) in enumerate(_STAGES):
        if stage_name_or_label in (label, name) or _STAGES[idx][0] == stage_name_or_label:
            return idx
    return 0


class UploadPipeline:
    """Stateful manager for the upload processing pipeline."""

    def __init__(
        self,
        ctx: WorkerContext,
        *,
        user_id: str,
        upload_id: str,
        quarantine_key: str,
        original_filename: str,
        mime_type: str,
        expected_sha256: str | None,
    ) -> None:
        self.ctx = ctx
        self.repo = UploadWorkerRepository(ctx)
        self.cache = UploadCacheRepository(ctx.redis)
        self.redis = ctx.redis

        self.user_id = user_id
        self.upload_id = upload_id
        self.quarantine_key = quarantine_key
        self.original_filename = original_filename
        self.mime_type = mime_type
        self.expected_sha256 = expected_sha256

        self.pipeline_start = time.monotonic()
        self.mime_category = _mime_cat(mime_type)

        self.status_key = f"upload:status:{quarantine_key}"
        self.event_channel = f"upload:events:{quarantine_key}"
        self.event_log_key = f"upload:eventlog:{quarantine_key}"

        self.completed_stage = 0
        self.tmp_path: Path | None = None
        self.pf: ProcessingFile | None = None

        self.initial_size = 0
        self.original_sha256 = ""
        self.cas_key = ""

        self.tracer = get_tracer()

    def _elapsed(self) -> float:
        return time.monotonic() - self.pipeline_start

    def _record_pipeline_metrics(self, status: str) -> None:
        elapsed = self._elapsed()
        upload_pipeline_total.labels(status=status, mime_category=self.mime_category).inc()
        upload_pipeline_duration.labels(status=status, mime_category=self.mime_category).observe(
            elapsed
        )

    async def emit_status(
        self,
        status: UploadStatus,
        detail: str | None = None,
        result: dict[str, Any] | None = None,
        stage_name_or_label: str | None = None,
        stage_percent: float = 0.0,
    ) -> None:
        payload: dict[str, Any] = {
            "upload_id": self.upload_id,
            "file_key": self.quarantine_key,
            "status": status,
            "detail": detail,
            "result": result,
        }
        if stage_name_or_label is not None:
            stage_index = _get_stage_index(stage_name_or_label)
            payload["stage_index"] = stage_index
            payload["stage_total"] = _STAGE_TOTAL
            payload["stage_percent"] = round(stage_percent, 4)
            payload["overall_percent"] = _overall(stage_index, stage_percent)

        payload_json = json.dumps(payload)
        await self.cache.emit_event(
            self.status_key, self.event_channel, self.event_log_key, payload_json
        )

    async def _fail_upload(self, detail: str, status: UploadStatus = UploadStatus.FAILED) -> None:
        """Helper to transition upload to FAILED/MALICIOUS status and record it."""
        await self.emit_status(status, detail=detail)
        status_str = "malicious" if status == UploadStatus.MALICIOUS else "failed"
        await self.repo.update_upload_status(self.upload_id, status_str, error_detail=detail)

    async def _check_deadline(self, stage_name: str) -> None:
        elapsed = self._elapsed()
        if elapsed > settings.upload_pipeline_max_seconds:
            msg = f"Pipeline deadline exceeded at stage '{stage_name}' ({elapsed:.0f}s)"
            raise UploadError(UploadStatus.FAILED, msg)

    async def _cancel_current_upload(self, where: str) -> None:
        logger.info("Upload %s cancelled %s", self.upload_id, where)
        await self._fail_upload("Upload cancelled by user")
        try:
            await delete_object(self.quarantine_key)
        except Exception as exc:
            logger.warning("Failed to delete quarantined object on cancel: %s", exc)

    async def _run_stages(self) -> None:
        """Core pipeline execution logic.

        Stage 1-2: scan + strip (the security gate — user blocks until here).
        Stage 5:   fast finalize — upload uncompressed stripped file to CAS,
                   emit CLEAN so the user is immediately unblocked, then enqueue
                   process_upload_post_scan for background compression/thumbnailing.
        """
        # Checkpoint 1: Metadata Strip + Scan
        if self.completed_stage < 2:
            await self._check_deadline("scan_strip")
            if self.pf is None or self.tmp_path is None:
                raise UploadError(UploadStatus.FAILED, "Pipeline state missing at scan_strip stage")
            if self.completed_stage == 1:
                await self.emit_status(
                    UploadStatus.PROCESSING,
                    detail="Stripping metadata",
                    stage_name_or_label="stripping",
                    stage_percent=0.5,
                )
                await run_strip_only(
                    self.pf, self.tmp_path, self.mime_type, self.upload_id, self.tracer
                )
            else:
                await self.emit_status(
                    UploadStatus.PROCESSING,
                    detail="Scanning for malware",
                    stage_name_or_label="scanning",
                    stage_percent=0.0,
                )
                scan_heartbeat_task = asyncio.create_task(
                    self._scan_heartbeat(interval=4.0, max_duration=120.0)
                )
                try:
                    await run_scan_and_strip(
                        self.ctx,
                        self.pf,
                        self.tmp_path,
                        self.original_filename,
                        self.original_sha256,
                        self.mime_type,
                        self.mime_category,
                        self.upload_id,
                        self.tracer,
                    )
                finally:
                    scan_heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await scan_heartbeat_task
            await self.repo.checkpoint_pipeline_stage(self.upload_id, 2)

        if self.pf is None:
            raise UploadError(UploadStatus.FAILED, "Pipeline state missing after scan+strip stage")
        await run_post_strip_pdf_check(self.pf, self.mime_type)
        await self._check_cancellation("after scan+strip stage")

        # Checkpoint 5: Fast finalize (idempotent via completed_stage guard).
        # Uploads the stripped (uncompressed) file to CAS so the user can
        # immediately add it to a PR draft.  Background compression/thumbnail
        # is handled by process_upload_post_scan.
        if self.completed_stage < 5:
            await self._fast_finalize_and_enqueue_post_scan()
            await self.repo.checkpoint_pipeline_stage(self.upload_id, 5)

    async def _scan_heartbeat(self, interval: float = 4.0, max_duration: float = 120.0) -> None:
        """Emit incremental scan progress events while the scanner is running.

        Advances stage_percent from 0.05 to 0.90 over max_duration seconds so the
        progress bar visibly moves while the scanner works in the background.
        """
        elapsed = 0.0
        while elapsed < max_duration:
            await asyncio.sleep(interval)
            elapsed += interval
            # Ease toward 0.90 so the final jump to 1.0 happens when scan completes
            stage_percent = min(0.90, elapsed / max_duration)
            await self.emit_status(
                UploadStatus.PROCESSING,
                detail="Scanning for malware",
                stage_name_or_label="scanning",
                stage_percent=stage_percent,
            )

    async def _check_cancellation(self, where: str) -> None:
        cancel_key = f"{_CANCEL_KEY_PREFIX}{self.upload_id}"
        if await self.cache.is_cancelled(cancel_key):
            await self._cancel_current_upload(where)
            # We raise a special error to stop execution but it's handled gracefully
            raise UploadError(UploadStatus.FAILED, "Upload cancelled by user")

    async def _fast_finalize_and_enqueue_post_scan(self) -> None:
        """Upload the stripped (uncompressed) file to CAS, emit CLEAN, enqueue post-scan job.

        This unblocks the user immediately after the scan gate passes.
        Compression and thumbnail generation happen in process_upload_post_scan
        running in the background.  The quarantine object is NOT deleted here —
        the post-scan job re-downloads it to compress, then deletes it.
        """
        await self._check_deadline("finalizing")
        await self.emit_status(
            UploadStatus.PROCESSING,
            detail="Finalising upload",
            stage_name_or_label="finalizing",
            stage_percent=0.0,
        )

        if self.pf is None:
            raise UploadError(UploadStatus.FAILED, "Pipeline state missing at finalizing stage")

        final_input = FinalizeInput(
            pf=self.pf,
            user_id=self.user_id,
            upload_id=self.upload_id,
            original_filename=self.original_filename,
            original_sha256=self.original_sha256,
            cas_key=self.cas_key,
            initial_size=self.initial_size,
            final_mime=self.mime_type,
            content_encoding=None,
            thumbnail_path=None,  # generated later by post-scan job
        )
        final_res = await run_finalize_storage(final_input, self.redis, self.tracer)

        res_data = {
            "file_key": final_res.final_key,
            "file_name": final_res.safe_name,
            "size": final_res.final_size,
            "original_size": self.initial_size,
            "mime_type": self.mime_type,
            "content_encoding": None,
            "processing_status": "pending",
        }
        await self.emit_status(
            UploadStatus.CLEAN,
            detail="File ready — optimising in background",
            result=res_data,
            stage_name_or_label="finalizing",
            stage_percent=1.0,
        )

        await self.repo.update_upload_status(
            self.upload_id,
            "clean",
            sha256=self.original_sha256,
            content_sha256=final_res.content_sha256,
            final_key=final_res.final_key,
            thumbnail_key=None,
            cas_key=final_res.db_cas_key,
            cas_ref_count=final_res.new_cas_ref if final_res.new_cas_ref > 0 else None,
            processing_status="pending",
        )

        from app.core import redis as redis_core

        # Fire-and-forget MalwareBazaar check — runs against the original SHA-256,
        # independent of whether the file is later compressed.  If Bazaar flags it,
        # retroactive_quarantine() handles the cleanup.
        if settings.bazaar_async_enabled and redis_core.arq_pool is not None:
            try:
                await redis_core.arq_pool.enqueue_job(
                    "check_bazaar",
                    upload_id=self.upload_id,
                    sha256=self.original_sha256,
                    cas_s3_key=final_res.final_key,
                    user_id=self.user_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to enqueue check_bazaar for upload %s: %s — Bazaar check skipped.",
                    self.upload_id,
                    exc,
                )
        elif settings.bazaar_async_enabled:
            logger.warning(
                "arq_pool unavailable — check_bazaar skipped for upload %s.",
                self.upload_id,
            )

        # Enqueue background compression + thumbnail job.
        if redis_core.arq_pool is not None:
            try:
                await redis_core.arq_pool.enqueue_job(
                    "process_upload_post_scan",
                    upload_id=self.upload_id,
                    user_id=self.user_id,
                    quarantine_key=self.quarantine_key,
                    original_filename=self.original_filename,
                    mime_type=self.mime_type,
                    original_sha256=self.original_sha256,
                    cas_key=self.cas_key,
                    cas_s3_key=final_res.final_key,
                    initial_size=self.initial_size,
                )
            except Exception as exc:
                logger.error(
                    "Failed to enqueue process_upload_post_scan for upload %s: %s — "
                    "file will remain uncompressed and without thumbnail.",
                    self.upload_id,
                    exc,
                )
                # Mark as degraded immediately since we can't schedule the optimization.
                await self.repo.update_processing_status(self.upload_id, "degraded")
        else:
            logger.warning(
                "arq_pool unavailable — post-scan processing skipped for upload %s. "
                "File will remain uncompressed.",
                self.upload_id,
            )
            await self.repo.update_processing_status(self.upload_id, "degraded")

        self._record_pipeline_metrics("clean")
        upload_file_size.labels(mime_category=self.mime_category).observe(self.initial_size)

    async def run(self) -> None:
        self.completed_stage = await self.repo.get_pipeline_stage(self.upload_id)
        if self.completed_stage > 0:
            logger.info("Resuming upload %s from stage %d", self.upload_id, self.completed_stage)

        try:
            await self._check_cancellation("before start")
        except UploadError:
            return

        stage_name, stage_label, _ = _STAGES[0]
        await self.emit_status(
            UploadStatus.PROCESSING, detail=stage_label, stage_name_or_label=stage_name
        )
        await self.repo.update_upload_status(self.upload_id, "processing")

        tmp = NamedTemporaryFile(delete=False)
        self.tmp_path = Path(tmp.name)
        tmp.close()

        try:
            download_result = await run_download_and_validate(
                self.tmp_path,
                self.quarantine_key,
                self.original_filename,
                self.mime_type,
                self.expected_sha256,
                self.upload_id,
            )
            self.pf = download_result.pf
            self.original_sha256 = download_result.original_sha256
            self.initial_size = download_result.initial_size
            self.mime_type = download_result.actual_mime
            self.mime_category = download_result.mime_category
            self.cas_key = download_result.cas_key

            await self.repo.update_upload_status(
                self.upload_id, "processing", sha256=self.original_sha256
            )
            await self._run_stages()

        except UploadError as exc:
            if "cancelled" in exc.detail:
                return  # Already handled
            self._record_pipeline_metrics(
                "malicious" if exc.status == UploadStatus.MALICIOUS else "failed"
            )
            await self._fail_upload(exc.detail, exc.status)
        except Exception as exc:
            logger.exception("Error processing upload %s", self.quarantine_key)
            msg = "Internal processing error occurred. Please try again or contact support."
            await self._fail_upload(msg)
            self._record_pipeline_metrics("failed")

            if self.ctx.job_try >= _MAX_ARQ_RETRIES:
                await self.repo.insert_dead_letter(
                    self.upload_id,
                    job_name="process_upload",
                    payload={
                        "user_id": self.user_id,
                        "upload_id": self.upload_id,
                        "quarantine_key": self.quarantine_key,
                        "original_filename": self.original_filename,
                        "mime_type": self.mime_type,
                    },
                    error=str(exc),
                    attempts=self.ctx.job_try,
                )

                try:
                    await delete_object(self.quarantine_key)
                except Exception as del_exc:
                    logger.warning(
                        "Failed to clean up quarantine object %s: %s", self.quarantine_key, del_exc
                    )
        finally:
            if self.pf is not None:
                self.pf.cleanup()
            elif self.tmp_path is not None:
                try:
                    self.tmp_path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("Failed to delete tmp path %s: %s", self.tmp_path, exc)
