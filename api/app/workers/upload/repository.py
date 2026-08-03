import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

import app.core.database.redis as redis_core
from app.models.dead_letter import DeadLetterJob
from app.models.upload import Upload
from app.services.auth import get_full_auth_config
from app.workers.upload.context import WorkerContext

logger = logging.getLogger(__name__)


async def _retry_db[T](
    operation: Callable[[], Awaitable[T]],
    max_attempts: int = 3,
    base_delay: float = 0.5,
    context: str = "DB operation",
) -> T:
    """Helper to retry a database operation with exponential backoff."""
    for attempt in range(max_attempts):
        try:
            return await operation()
        except Exception as exc:
            if attempt == max_attempts - 1:
                logger.error("%s failed after %d attempts: %s", context, max_attempts, exc)
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "%s retry %d/%d in %.1fs: %s", context, attempt + 1, max_attempts, delay, exc
            )
            await asyncio.sleep(delay)
    raise RuntimeError("Unreachable")


class UploadWorkerRepository:
    """Database-facing operations used by the upload worker pipeline."""

    def __init__(self, ctx: WorkerContext) -> None:
        self._ctx = ctx

    def _session_factory(self) -> Any:
        return self._ctx.db_sessionmaker

    async def update_upload_status(
        self,
        upload_id: str,
        status: str,
        *,
        sha256: str | None = None,
        content_sha256: str | None = None,
        final_key: str | None = None,
        thumbnail_key: str | None = None,
        thumbnail_status: str | None = None,
        error_detail: str | None = None,
        cas_key: str | None = None,
        cas_ref_count: int | None = None,
        processing_status: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
    ) -> bool:
        """Persist a lifecycle transition without overwriting cancellation."""
        session_factory = self._session_factory()
        if session_factory is None:
            return True

        async def _do_update() -> bool:
            async with session_factory() as session:
                values: dict[str, Any] = {
                    "status": status,
                    "updated_at": datetime.now(UTC),
                }
                if sha256 is not None:
                    values["sha256"] = sha256
                if content_sha256 is not None:
                    values["content_sha256"] = content_sha256
                if final_key is not None:
                    values["final_key"] = final_key
                if thumbnail_key is not None:
                    values["thumbnail_key"] = thumbnail_key
                if thumbnail_status is not None:
                    values["thumbnail_status"] = thumbnail_status
                if error_detail is not None:
                    values["error_detail"] = error_detail
                if cas_key is not None:
                    values["cas_key"] = cas_key
                if cas_ref_count is not None:
                    values["cas_ref_count"] = cas_ref_count
                if processing_status is not None:
                    values["processing_status"] = processing_status
                if mime_type is not None:
                    values["mime_type"] = mime_type
                if size_bytes is not None:
                    values["size_bytes"] = size_bytes

                statement = update(Upload).where(Upload.upload_id == upload_id)
                if status != "cancelled":
                    statement = statement.where(Upload.status != "cancelled")
                result = await session.execute(statement.values(**values))
                await session.commit()
                return bool(getattr(result, "rowcount", 0))

        return await _retry_db(_do_update, context=f"update_upload_status for {upload_id}")

    async def publish_clean_upload(
        self,
        upload_id: str,
        *,
        sha256: str,
        content_sha256: str,
        final_key: str,
        cas_key: str,
        cas_ref_count: int | None,
    ) -> bool:
        """Publish CLEAN unless an authoritative cancellation already won."""
        session_factory = self._session_factory()
        if session_factory is None:
            return True

        async def _do_update() -> bool:
            async with session_factory() as session:
                result = await session.execute(
                    update(Upload)
                    .where(Upload.upload_id == upload_id, Upload.status != "cancelled")
                    .values(
                        status="clean",
                        updated_at=datetime.now(UTC),
                        sha256=sha256,
                        content_sha256=content_sha256,
                        final_key=final_key,
                        cas_key=cas_key,
                        cas_ref_count=cas_ref_count,
                        processing_status="pending",
                    )
                )
                await session.commit()
                return bool(result.rowcount)

        return await _retry_db(_do_update, context=f"publish_clean_upload for {upload_id}")

    async def update_processing_status(self, upload_id: str, processing_status: str) -> None:
        """Best-effort update of processing_status only (does not touch status)."""
        session_factory = self._session_factory()
        if session_factory is None:
            return

        async def _do_update() -> None:
            async with session_factory() as session:
                await session.execute(
                    update(Upload)
                    .where(Upload.upload_id == upload_id)
                    .values(processing_status=processing_status, updated_at=datetime.now(UTC))
                )
                await session.commit()

        with contextlib.suppress(Exception):
            await _retry_db(_do_update, context=f"update_processing_status for {upload_id}")

    async def checkpoint_pipeline_stage(self, upload_id: str, stage: int) -> None:
        """Persist a completed pipeline stage for resume-on-retry behavior."""
        session_factory = self._session_factory()
        if session_factory is None:
            return

        async def _do_checkpoint() -> None:
            async with session_factory() as session:
                await session.execute(
                    update(Upload).where(Upload.upload_id == upload_id).values(pipeline_stage=stage)
                )
                await session.commit()

        with contextlib.suppress(Exception):
            await _retry_db(_do_checkpoint, context=f"checkpoint_pipeline_stage for {upload_id}")

    async def get_pipeline_stage(self, upload_id: str) -> int:
        """Read the last completed pipeline stage from the DB."""
        session_factory = self._session_factory()
        if session_factory is None:
            return 0

        async def _do_get() -> int:
            async with session_factory() as session:
                row = await session.scalar(select(Upload).where(Upload.upload_id == upload_id))
                return row.pipeline_stage if row else 0

        try:
            return await _retry_db(_do_get, context=f"get_pipeline_stage for {upload_id}")
        except Exception:
            return 0

    async def get_auth_config(self) -> dict[str, Any]:
        """Fetch full auth config from DB with fallback defaults."""
        session_factory = self._session_factory()
        if session_factory is None:
            return {}

        async def _do_get() -> dict[str, Any]:
            async with session_factory() as session:
                return await get_full_auth_config(session)

        try:
            return await _retry_db(_do_get, context="get_auth_config in worker")
        except Exception:
            return {}

    async def insert_dead_letter(
        self,
        upload_id: str,
        job_name: str,
        payload: dict[str, Any],
        error: str,
        attempts: int,
    ) -> None:
        """Insert a failed worker job into dead_letter_jobs."""
        session_factory = self._session_factory()
        if session_factory is None:
            logger.error(
                "No DB session factory - cannot insert dead letter for upload %s",
                upload_id,
            )
            return

        async def _do_insert() -> None:
            async with session_factory() as session:
                dlj = DeadLetterJob(
                    job_name=job_name,
                    upload_id=upload_id,
                    payload=payload,
                    error_detail=error[:4000] if error else None,
                    attempts=attempts,
                )
                session.add(dlj)
                await session.commit()

        try:
            await _retry_db(_do_insert, context=f"insert_dead_letter for {upload_id}")
            logger.info(
                "Dead-lettered job %s for upload %s after %d attempts",
                job_name,
                upload_id,
                attempts,
            )
        except Exception:
            pass

    async def maybe_dispatch_webhook(self, upload_id: str) -> None:
        """Enqueue webhook dispatch if the upload row has a webhook_url."""
        session_factory = self._session_factory()
        if session_factory is None:
            return

        async def _check_webhook() -> str | None:
            async with session_factory() as session:
                row = await session.scalar(select(Upload).where(Upload.upload_id == upload_id))
                return row.webhook_url if row else None

        try:
            webhook_url = await _retry_db(
                _check_webhook, context=f"maybe_dispatch_webhook for {upload_id}"
            )
            if not webhook_url:
                return

            if redis_core.arq_pool is None:
                logger.warning("arq_pool unavailable - webhook for upload %s skipped", upload_id)
                return

            await redis_core.arq_pool.enqueue_job("dispatch_webhook", upload_id=upload_id)
        except Exception as exc:
            logger.warning("Failed to enqueue webhook for upload %s: %s", upload_id, exc)
