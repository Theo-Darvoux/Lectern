import asyncio
import contextlib
import inspect
import json
import logging
import time
from pathlib import Path
from typing import Any, cast

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.core.database.redis import redis_lock
from app.core.events.processing import ProcessingFile
from app.core.observability.metrics import mime_category as _mime_cat
from app.core.observability.metrics import (
    upload_file_size,
    upload_pipeline_duration,
    upload_pipeline_total,
)
from app.core.observability.telemetry import get_tracer
from app.core.security.cas import decrement_cas_ref
from app.core.security.processing_paths import make_processing_temp_path
from app.core.security.scanner import MalwareScanner
from app.core.storage.facade import delete_object
from app.routers.upload.cancellation import upload_lifecycle_lock_name
from app.schemas.material import UploadStatus
from app.workers.upload.cache_repo import UploadCacheRepository
from app.workers.upload.constants import (
    _CANCEL_KEY_PREFIX,
    _MAX_ARQ_RETRIES,
    _SHA256_CACHE_PREFIX,
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

logger = logging.getLogger(__name__)


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
        try:
            from app.routers.upload.helpers import _release_storage_reservation

            await _release_storage_reservation(self.upload_id, self.redis)
        except Exception as exc:
            # Capacity reservations self-expire and are reconciled by the next reserve.
            logger.warning(
                "Failed to release storage reservation for upload %s: %s",
                self.upload_id,
                exc,
            )

    def _remaining_pipeline_seconds(self, stage_name: str) -> float:
        elapsed = self._elapsed()
        remaining = float(settings.upload_pipeline_max_seconds) - elapsed
        if remaining <= 0:
            msg = f"Pipeline deadline exceeded at stage '{stage_name}' ({elapsed:.0f}s)"
            raise UploadError(UploadStatus.FAILED, msg)
        return remaining

    def _check_deadline(self, stage_name: str) -> None:
        self._remaining_pipeline_seconds(stage_name)

    async def _cancel_current_upload(self, where: str) -> None:
        logger.info("Upload %s cancelled %s", self.upload_id, where)
        await self._fail_upload("Upload cancelled by user")
        try:
            await self._delete_quarantine_object()
        except Exception as exc:
            logger.warning("Failed to delete quarantined object on cancel: %s", exc)

    async def _delete_quarantine_object(self) -> None:
        """Delete quarantine bytes and release their upload-quota membership."""
        await delete_object(self.quarantine_key)
        try:
            await self.redis.zrem(f"quota:uploads:{self.user_id}", self.quarantine_key)
        except Exception as exc:
            logger.warning(
                "Deleted quarantine object %s but failed to release quota membership: %s",
                self.quarantine_key,
                exc,
            )

    async def _run_stages(self) -> None:
        """Core pipeline execution logic.

        Stage 1-2: scan + strip (the security gate — user blocks until here).
        Stage 5:   fast finalize — upload stripped file to immutable CAS,
                   emit CLEAN so the user is immediately unblocked, then enqueue
                   process_upload_post_scan for background thumbnailing.
        """
        # Checkpoint 1: Metadata Strip + Scan
        if self.completed_stage < 2:
            self._check_deadline("scan_strip")
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
        # Uploads the stripped file to immutable CAS so the user can
        # immediately add it to a PR draft. Background thumbnail generation is
        # handled by process_upload_post_scan.
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
        redis_cancelled = await self.cache.is_cancelled(cancel_key)
        db_cancelled = False
        session_factory = self.ctx.db_sessionmaker
        if isinstance(session_factory, async_sessionmaker) or inspect.isfunction(session_factory):
            db_cancelled = await self.repo.is_upload_cancelled(self.upload_id)
        if redis_cancelled or db_cancelled:
            await self._cancel_current_upload(where)
            # We raise a special error to stop execution but it's handled gracefully
            raise UploadError(UploadStatus.FAILED, "Upload cancelled by user")

    async def _fast_finalize_and_enqueue_post_scan(self) -> None:
        """Publish CLEAN and schedule follow-up work without losing cancellation."""
        self._check_deadline("finalizing")
        await self.emit_status(
            UploadStatus.PROCESSING,
            detail="Finalising upload",
            stage_name_or_label="finalizing",
            stage_percent=0.0,
        )

        if self.pf is None:
            raise UploadError(UploadStatus.FAILED, "Pipeline state missing at finalizing stage")

        await self._check_bazaar_before_finalize()
        has_authoritative_db = self.ctx.db_sessionmaker is not None
        if has_authoritative_db:
            await self._check_cancellation("immediately before final publication")

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
            thumbnail_path=None,
        )
        finalize_deadline = asyncio.timeout(
            self._remaining_pipeline_seconds("finalizing")
        )
        try:
            async with finalize_deadline:
                final_res = await run_finalize_storage(final_input, self.redis, self.tracer)
        except TimeoutError as exc:
            # Only translate cancellation caused by our end-to-end deadline. A
            # backend TimeoutError remains a backend error rather than being
            # mislabeled as pipeline exhaustion.
            if not finalize_deadline.expired():
                raise
            raise UploadError(
                UploadStatus.FAILED,
                f"Pipeline deadline exceeded at stage 'finalizing' ({self._elapsed():.0f}s)",
            ) from exc

        res_data = {
            "file_key": final_res.final_key,
            "file_name": final_res.safe_name,
            "size": final_res.final_size,
            "original_size": self.initial_size,
            "mime_type": self.mime_type,
            "content_encoding": None,
            "processing_status": "pending",
        }
        published = await self.repo.publish_clean_upload(
            self.upload_id,
            sha256=self.original_sha256,
            content_sha256=final_res.content_sha256,
            final_key=final_res.final_key,
            cas_key=final_res.db_cas_key,
            cas_ref_count=final_res.new_cas_ref if final_res.new_cas_ref > 0 else None,
        )
        if not published:
            # Cancellation committed first. Release the CAS reference acquired by
            # finalization using an idempotent operation ID.
            await decrement_cas_ref(
                self.redis,
                final_res.content_sha256,
                operation_id=f"upload-finalize:{self.upload_id}:cancel-compensation",
            )
            await self.redis.zrem(
                f"quota:uploads:{self.user_id}",
                f"staging:{self.user_id}:{self.upload_id}",
            )
            raise UploadError(UploadStatus.FAILED, "Upload cancelled by user")

        # Publication and cancellation race through the conditional DB update.
        # After publication, compete for the shared lifecycle lock once more:
        # a cancellation that is already pending can acquire it first, release
        # CAS ownership, and set the marker before CLEAN is emitted or follow-up
        # work is scheduled. If this worker acquires it first, publication wins
        # and a later cancellation will clear the cached CLEAN state and CAS ref.
        lifecycle_guard = (
            redis_lock(
                cast(Any, self.redis),
                upload_lifecycle_lock_name(self.upload_id),
                timeout=120.0,
                expire=300.0,
            )
            if has_authoritative_db
            else contextlib.nullcontext()
        )
        async with lifecycle_guard:
            if has_authoritative_db:
                await self._check_cancellation("after clean publication")
            try:
                await self.redis.set(
                    f"{_SHA256_CACHE_PREFIX}{self.user_id}:{self.original_sha256}",
                    final_res.final_key,
                    ex=24 * 3600,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to publish personal dedup cache for upload %s: %s",
                    self.upload_id,
                    exc,
                )
            await self.emit_status(
                UploadStatus.CLEAN,
                detail="File ready — optimising in background",
                result=res_data,
                stage_name_or_label="finalizing",
                stage_percent=1.0,
            )
            if has_authoritative_db:
                await self._check_cancellation("after CLEAN emission")

            import app.core.database.redis as redis_core

            if (
                settings.bazaar_async_enabled
                and not settings.malwarebazaar_fail_closed
                and redis_core.arq_pool is not None
            ):
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
            elif settings.bazaar_async_enabled and not settings.malwarebazaar_fail_closed:
                logger.warning(
                    "arq_pool unavailable — check_bazaar skipped for upload %s.",
                    self.upload_id,
                )

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
                        "file will remain available without a thumbnail.",
                        self.upload_id,
                        exc,
                    )
                    try:
                        await self.repo.update_processing_status(self.upload_id, "degraded")
                    except Exception as status_exc:
                        logger.warning(
                            "Failed to mark post-scan processing degraded for upload %s: %s",
                            self.upload_id,
                            status_exc,
                        )
            else:
                logger.warning(
                    "arq_pool unavailable — post-scan processing skipped for upload %s. "
                    "File will remain available without a thumbnail.",
                    self.upload_id,
                )
                try:
                    await self.repo.update_processing_status(self.upload_id, "degraded")
                except Exception as status_exc:
                    logger.warning(
                        "Failed to mark post-scan processing degraded for upload %s: %s",
                        self.upload_id,
                        status_exc,
                    )

            self._record_pipeline_metrics("clean")
            upload_file_size.labels(mime_category=self.mime_category).observe(self.initial_size)

    async def _check_bazaar_before_finalize(self) -> None:
        """Honor fail-closed and legacy synchronous Bazaar policy before publication."""
        if settings.bazaar_async_enabled and not settings.malwarebazaar_fail_closed:
            return
        if await self.redis.get(f"bazaar:clean:{self.original_sha256}"):
            return

        scanner = self.ctx.scanner
        owns_scanner = scanner is None
        if scanner is None:
            scanner = _get_fallback_scanner()
        try:
            threat = await scanner.check_malwarebazaar(self.original_sha256, self.original_filename)
        finally:
            if owns_scanner:
                await scanner.close()

        if threat is not None:
            raise UploadError(
                UploadStatus.MALICIOUS,
                f"Known malware detected: {threat}",
            )

    async def run(self) -> None:
        self.completed_stage = await self.repo.get_pipeline_stage(self.upload_id)
        if self.completed_stage > 0:
            logger.info("Resuming upload %s from stage %d", self.upload_id, self.completed_stage)

        try:
            await self._check_cancellation("before start")
        except UploadError:
            return

        if not await self.repo.update_upload_status(self.upload_id, "processing"):
            logger.info("Upload %s was cancelled before processing began", self.upload_id)
            return

        stage_name, stage_label, _ = _STAGES[0]
        await self.emit_status(
            UploadStatus.PROCESSING, detail=stage_label, stage_name_or_label=stage_name
        )

        self.tmp_path = make_processing_temp_path(prefix="upload-pipeline-")

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
                    await self._delete_quarantine_object()
                except Exception as del_exc:
                    logger.warning(
                        "Failed to clean up quarantine object %s: %s", self.quarantine_key, del_exc
                    )
            else:
                raise
        finally:
            if self.pf is not None:
                self.pf.cleanup()
            elif self.tmp_path is not None:
                try:
                    self.tmp_path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("Failed to delete tmp path %s: %s", self.tmp_path, exc)
