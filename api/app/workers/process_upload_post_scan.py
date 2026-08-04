"""Background post-scan processing: thumbnail and publish derived metadata.

Called by process_upload after the scan gate passes.  The file is already in
CAS (without a thumbnail) and the upload row is status=clean,
processing_status=pending.

Failure behaviour:
  - Thumbnail failures are soft: the sanitized CAS object remains accessible.
  - A second metadata-strip failure preserves the original sanitized CAS bytes and
    marks post-processing degraded.
  - Unexpected failures retry up to ``_POST_MAX_RETRIES`` before dead-lettering.
  - A settled state (complete | degraded) triggers pending auto-merge PRs.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database.post_commit import (
    PostCommitKey,
    dispatch_post_commit_actions,
    persist_post_commit_jobs,
)
from app.core.database.redis import redis_lock
from app.core.events.sse import broadcast_to_topic
from app.core.observability.telemetry import get_tracer
from app.core.storage.facade import delete_object, upload_file_multipart
from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
from app.models.upload import Upload
from app.routers.upload.cancellation import upload_cancel_key, upload_lifecycle_lock_name
from app.schemas.material import UploadStatus
from app.services.notification import notify_user
from app.services.pr import (
    _cleanup_pr_resources,
    _pr_directory_topics,
    apply_pr,
    get_pr_all_file_keys,
)
from app.workers.upload.cache_repo import UploadCacheRepository
from app.workers.upload.constants import _STATUS_CACHE_PREFIX
from app.workers.upload.context import WorkerContext
from app.workers.upload.repository import UploadWorkerRepository
from app.workers.upload.stages.download import run_download_and_validate
from app.workers.upload.stages.scan_strip import run_strip_only
from app.workers.upload.stages.thumbnail import run_thumbnail_stage

logger = logging.getLogger(__name__)

_POST_MAX_RETRIES = 3
# Max total attempts for thumbnail generation (includes initial attempt + retries).
_THUMB_MAX_ATTEMPTS = 2
# Settled processing statuses — a PR can auto-merge when all its files reach one of these.
_SETTLED_STATUSES = frozenset({"complete", "degraded"})


async def _publish_postprocessed_upload(
    worker_ctx: WorkerContext,
    *,
    upload_id: str,
    update_values: dict[str, Any],
) -> bool:
    """Serialize the completion transition against retroactive quarantine."""
    session_factory = worker_ctx.db_sessionmaker
    if session_factory is None:
        return False

    async with session_factory() as session:
        upload = await session.scalar(
            select(Upload).where(Upload.upload_id == upload_id).with_for_update()
        )
        if upload is None or upload.status != "clean" or int(upload.cas_ref_count or 0) <= 0:
            logger.warning(
                "Skipping post-scan publication for upload %s in status %s with CAS refs %s",
                upload_id,
                upload.status if upload is not None else "missing",
                upload.cas_ref_count if upload is not None else "missing",
            )
            return False
        if await worker_ctx.redis.exists(upload_cancel_key(upload_id)) == 1:
            logger.info("Skipping post-scan publication for cancelled upload %s", upload_id)
            return False

        for key, value in update_values.items():
            setattr(upload, key, value)
        upload.status = "clean"
        await session.commit()
        return True


def _post_scan_lifecycle_guard(worker_ctx: WorkerContext, upload_id: str) -> Any:
    # Production uses SQLAlchemy's async_sessionmaker. The integration fixture
    # supplies a plain function returning an AsyncSession bound to its test
    # transaction. Mock callables are intentionally excluded because they do
    # not provide a real Redis lock or authoritative database state.
    session_factory = worker_ctx.db_sessionmaker
    if not (isinstance(session_factory, async_sessionmaker) or inspect.isfunction(session_factory)):
        return contextlib.nullcontext()
    return redis_lock(
        cast(Any, worker_ctx.redis),
        upload_lifecycle_lock_name(upload_id),
        timeout=120.0,
        expire=300.0,
    )


async def _settle_degraded_post_scan(
    worker_ctx: WorkerContext,
    repo: UploadWorkerRepository,
    *,
    upload_id: str,
    cas_s3_key: str,
) -> None:
    async with _post_scan_lifecycle_guard(worker_ctx, upload_id):
        if not await repo.update_processing_status(upload_id, "degraded"):
            return
        if await worker_ctx.redis.exists(upload_cancel_key(upload_id)) == 1:
            return
        await _trigger_pending_auto_merges(worker_ctx, cas_s3_key)


async def process_upload_post_scan(
    ctx: dict[str, Any],
    upload_id: str,
    user_id: str,
    quarantine_key: str,
    original_filename: str,
    mime_type: str,
    original_sha256: str,
    cas_key: str,
    cas_s3_key: str,
    initial_size: int,
) -> None:
    """Generate derived metadata for a previously scanned, immutable CAS object.

    The quarantine object is re-downloaded and stripped again before thumbnailing.
    The CAS object created by the scan job is never overwritten.

    A thumbnail failure is soft; unexpected infrastructure failures retry the job.
    """
    worker_ctx = WorkerContext.from_arq_ctx(ctx)
    repo = UploadWorkerRepository(worker_ctx)
    tracer = get_tracer()
    job_try: int = ctx.get("job_try", 1)

    if not await repo.update_processing_status(upload_id, "running"):
        return

    tmp = NamedTemporaryFile(delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    pf = None
    thumbnail_path: str | None = None

    try:
        # ── 1. Re-download from quarantine ───────────────────────────────────
        try:
            download_result = await run_download_and_validate(
                tmp_path,
                quarantine_key,
                original_filename,
                mime_type,
                expected_sha256=original_sha256,
                upload_id=upload_id,
            )
        except Exception as exc:
            logger.error(
                "Post-scan download from quarantine failed for upload %s: %s — "
                "sanitized file remains available in CAS.",
                upload_id,
                exc,
            )
            await _settle_degraded_post_scan(
                worker_ctx,
                repo,
                upload_id=upload_id,
                cas_s3_key=cas_s3_key,
            )
            return

        pf = download_result.pf
        actual_mime = download_result.actual_mime
        auth_config = await repo.get_auth_config()

        # ── 2. Re-strip metadata (idempotent) ────────────────────────────────
        try:
            await run_strip_only(pf, tmp_path, actual_mime, upload_id, tracer)
        except Exception as exc:
            logger.error(
                "Post-scan metadata strip failed for upload %s: %s — preserving existing "
                "sanitized CAS object.",
                upload_id,
                exc,
            )
            await _settle_degraded_post_scan(
                worker_ctx,
                repo,
                upload_id=upload_id,
                cas_s3_key=cas_s3_key,
            )
            return

        # ── 3. Generate thumbnail (soft failure — retried, then accepted without) ─
        thumbnail_status: str = "skipped"
        for _thumb_attempt in range(1, _THUMB_MAX_ATTEMPTS + 1):
            try:
                thumbnail_path = await run_thumbnail_stage(
                    pf, actual_mime, original_filename, tracer, config=auth_config
                )
                # None means unsupported type — not a failure, no retry needed.
                thumbnail_status = "ok" if thumbnail_path else "skipped"
                break
            except Exception as exc:
                if _thumb_attempt < _THUMB_MAX_ATTEMPTS:
                    logger.warning(
                        "Thumbnail attempt %d/%d failed for upload %s: %s — retrying in 2s.",
                        _thumb_attempt,
                        _THUMB_MAX_ATTEMPTS,
                        upload_id,
                        exc,
                    )
                    await asyncio.sleep(2)
                else:
                    logger.warning(
                        "Thumbnail generation failed for upload %s after %d attempts: %s"
                        " — proceeding without thumbnail.",
                        upload_id,
                        _THUMB_MAX_ATTEMPTS,
                        exc,
                    )
                    thumbnail_status = "failed"

        # CAS objects are immutable and already contain the sanitized bytes.
        # Background processing adds a thumbnail but never rewrites the CAS key.
        # ── 6. Upload thumbnail ───────────────────────────────────────────────
        thumbnail_key: str | None = None
        if thumbnail_path:
            cas_id = cas_s3_key.split("/", 1)[-1]
            _candidate_key = f"thumbnails/{cas_id}.webp"
            try:
                await asyncio.wait_for(
                    upload_file_multipart(
                        Path(thumbnail_path),
                        _candidate_key,
                        content_type="image/webp",
                    ),
                    timeout=30.0,
                )
                thumbnail_key = _candidate_key
            except Exception as exc:
                logger.warning(
                    "Thumbnail upload failed for upload %s: %s — skipping thumbnail.",
                    upload_id,
                    exc,
                )
                thumbnail_status = "failed"
            finally:
                with contextlib.suppress(Exception):
                    Path(thumbnail_path).unlink(missing_ok=True)
                thumbnail_path = None  # already cleaned up

        # ── 7. Persist results ────────────────────────────────────────────────
        update_kwargs: dict[str, Any] = {
            "processing_status": "complete",
            "thumbnail_status": thumbnail_status,
        }
        if thumbnail_key:
            update_kwargs["thumbnail_key"] = thumbnail_key

        published = False
        async with _post_scan_lifecycle_guard(worker_ctx, upload_id):
            published = await _publish_postprocessed_upload(
                worker_ctx,
                upload_id=upload_id,
                update_values=update_kwargs,
            )
            if published:
                # Read and publish the derived CLEAN payload while cancellation is
                # excluded by the same upload lifecycle lock.
                cache = UploadCacheRepository(worker_ctx.redis)
                status_key = f"{_STATUS_CACHE_PREFIX}{quarantine_key}"
                event_channel = f"upload:events:{quarantine_key}"
                event_log_key = f"upload:eventlog:{quarantine_key}"

                cached_json = await worker_ctx.redis.get(status_key)
                if isinstance(cached_json, (str, bytes, bytearray)) and cached_json:
                    payload = json.loads(cached_json)
                    if payload.get("result"):
                        payload["result"]["processing_status"] = "complete"
                        payload["status"] = UploadStatus.CLEAN
                        payload["detail"] = "Processing complete"
                        payload["stage_index"] = 4
                        payload["stage_percent"] = 1.0
                        payload["overall_percent"] = 100
                        await cache.emit_event(
                            status_key,
                            event_channel,
                            event_log_key,
                            json.dumps(payload),
                        )

                await repo.maybe_dispatch_webhook(upload_id)
                await _trigger_pending_auto_merges(worker_ctx, cas_s3_key)

        if not published:
            if thumbnail_key:
                try:
                    await delete_object(thumbnail_key)
                except Exception as exc:
                    logger.warning(
                        "Failed to remove unpublished thumbnail %s: %s",
                        thumbnail_key,
                        exc,
                    )
            return

        # ── 8. Delete quarantine object ───────────────────────────────────────
        try:
            await delete_object(quarantine_key)
            try:
                await worker_ctx.redis.zrem(f"quota:uploads:{user_id}", quarantine_key)
            except Exception as exc:
                logger.warning(
                    "Deleted quarantine %s but failed to release quota membership: %s",
                    quarantine_key,
                    exc,
                )
        except Exception as exc:
            logger.warning("Failed to delete quarantine %s: %s", quarantine_key, exc)

        logger.info(
            "Post-scan processing complete for upload %s (thumbnail=%s).",
            upload_id,
            thumbnail_key is not None,
        )

    except Exception as exc:
        # Hard failure — will be retried by arq unless we've hit the limit.
        logger.exception(
            "Post-scan processing failed for upload %s (attempt %d)", upload_id, job_try
        )

        if job_try >= _POST_MAX_RETRIES:
            async with _post_scan_lifecycle_guard(worker_ctx, upload_id):
                if await _handle_permanent_failure(repo, upload_id, cas_s3_key, exc, job_try):
                    await _trigger_pending_auto_merges(worker_ctx, cas_s3_key)
        else:
            # Reset to pending so status reflects "not yet settled" during retry wait.
            await repo.update_processing_status(upload_id, "pending")
            raise  # Let arq schedule the retry.

    finally:
        if pf is not None:
            pf.cleanup()
        elif tmp_path is not None:
            with contextlib.suppress(Exception):
                tmp_path.unlink(missing_ok=True)
        if thumbnail_path:
            with contextlib.suppress(Exception):
                Path(thumbnail_path).unlink(missing_ok=True)


async def _handle_permanent_failure(
    repo: UploadWorkerRepository,
    upload_id: str,
    cas_s3_key: str,
    exc: Exception,
    attempts: int,
) -> bool:
    """Mark the upload degraded unless authoritative cancellation already won."""
    if not await repo.update_processing_status(upload_id, "degraded"):
        return False
    await repo.insert_dead_letter(
        upload_id,
        job_name="process_upload_post_scan",
        payload={"upload_id": upload_id, "cas_s3_key": cas_s3_key},
        error=str(exc),
        attempts=attempts,
    )
    logger.error(
        "Post-scan processing permanently failed for upload %s after %d attempts — "
        "serving the sanitized CAS object without derived metadata. Dead-letter inserted.",
        upload_id,
        attempts,
    )
    return True


async def _trigger_pending_auto_merges(ctx: WorkerContext, cas_s3_key: str) -> None:
    """After this upload settles, check whether any deferred auto-merge PRs can now proceed.

    A PR is auto-merged when its author has auto_approve=True and every cas/ file
    referenced in the PR payload has reached a settled processing_status
    (complete or degraded).
    """
    if ctx.db_sessionmaker is None:
        return

    try:
        async with ctx.db_sessionmaker() as db:
            # Find the open PR with auto_merge_pending that claims this file.
            pr = await db.scalar(
                select(PullRequest)
                .join(PRFileClaim, PRFileClaim.pr_id == PullRequest.id)
                .where(
                    PRFileClaim.file_key == cas_s3_key,
                    PullRequest.auto_merge_pending.is_(True),
                    PullRequest.status == PRStatus.OPEN,
                )
                .with_for_update(skip_locked=True)
            )
            if pr is None:
                return

            # Gather every unique cas/ key in this PR's payload.
            # Deduplicate: two materials may reference the same CAS key (identical
            # file content deduped by hash), but there is only one Upload row, so
            # the DB count must be compared against the number of distinct keys.
            all_cas_keys = list({k for k in get_pr_all_file_keys(pr) if k.startswith("cas/")})
            if all_cas_keys:
                settled_count = await db.scalar(
                    select(func.count())
                    .select_from(Upload)
                    .where(
                        Upload.final_key.in_(all_cas_keys),
                        Upload.status.in_(("clean", "applied")),
                        Upload.processing_status.in_(list(_SETTLED_STATUSES)),
                    )
                )
                if settled_count is None or settled_count < len(all_cas_keys):
                    return  # Other files still processing — wait.

            # All files settled — execute auto-merge.
            pr.status = PRStatus.APPROVED
            pr.reviewed_by = pr.author_id
            pr.auto_merge_pending = False

            jobs: list[Any] = []
            db.info[PostCommitKey.JOBS] = jobs
            db.info.setdefault(PostCommitKey.JOB_KEYS, set())

            await apply_pr(db, pr)
            await _cleanup_pr_resources(db, pr, redis=ctx.redis)  # type: ignore[arg-type]

            sse_broadcasts = db.info.pop(PostCommitKey.SSE, [])
            event = {"type": "pr_closed", "id": str(pr.id)}
            for topic in _pr_directory_topics(list(pr.payload)):
                sse_broadcasts.append((topic, event))
            if pr.author_id:
                sse_broadcasts.append((f"pr_updates:{pr.author_id}", event))

            await persist_post_commit_jobs(db)
            await db.commit()

            for topic, event in sse_broadcasts:
                broadcast_to_topic(topic, event)

            # Dispatch jobs from the durable DB outbox. Queue outages leave
            # rows pending for the minute-level retry worker.
            await dispatch_post_commit_actions(db)

            if pr.author_id:
                async with ctx.db_sessionmaker() as notify_db:
                    await notify_user(
                        notify_db,
                        pr.author_id,
                        "pr_approved",
                        f'Your contribution "{pr.title}" was published',
                        link=f"/pull-requests/{pr.id}",
                    )
                    await notify_db.commit()

            logger.info("Auto-merged PR %s after all uploads settled.", pr.id)

    except Exception as exc:
        logger.error("Failed to trigger auto-merge for cas_s3_key=%s: %s", cas_s3_key, exc)
