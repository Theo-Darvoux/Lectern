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
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.common.exceptions import ConflictError
from app.core.database.post_commit import (
    PostCommitKey,
    dispatch_post_commit_actions,
    finalize_transaction_callbacks,
    persist_post_commit_jobs,
    rollback_transaction_callbacks,
)
from app.core.database.redis import redis_lock
from app.core.events.sse import broadcast_to_topic
from app.core.observability.telemetry import get_tracer
from app.core.security.async_utils import settle_awaitable
from app.core.security.processing_paths import make_processing_temp_path
from app.core.storage.facade import delete_object, upload_file_multipart
from app.core.storage.liveness import storage_lifecycle_lock
from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
from app.models.upload import Upload
from app.routers.upload.cancellation import upload_cancel_key, upload_lifecycle_lock_name
from app.schemas.material import UploadStatus
from app.services.notification import notify_user
from app.services.pr import (
    _cleanup_pr_resources,
    _lock_and_validate_pr_cas_files,
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
    settled = False
    async with _post_scan_lifecycle_guard(worker_ctx, upload_id):
        if not await repo.update_processing_status(upload_id, "degraded"):
            return
        if await worker_ctx.redis.exists(upload_cancel_key(upload_id)) == 1:
            return
        settled = True
    if settled:
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

    tmp_path = make_processing_temp_path(prefix="upload-post-scan-")

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
        # Hold the deterministic thumbnail lifecycle lock from physical write through
        # authoritative Upload.thumbnail_key publication. A stale admin prune must
        # therefore either delete first (and this retry rewrites) or observe the newly
        # published thumbnail and skip deletion.
        thumbnail_key: str | None = None
        thumbnail_candidate_key: str | None = None
        if thumbnail_path:
            cas_id = cas_s3_key.split("/", 1)[-1]
            thumbnail_candidate_key = f"thumbnails/{cas_id}/{upload_id}.webp"

        published = False
        async with contextlib.AsyncExitStack() as storage_guard:
            if thumbnail_candidate_key is not None:
                assert thumbnail_path is not None
                await storage_guard.enter_async_context(
                    storage_lifecycle_lock(worker_ctx.db_sessionmaker, thumbnail_candidate_key)
                )
                try:
                    await asyncio.wait_for(
                        upload_file_multipart(
                            Path(thumbnail_path),
                            thumbnail_candidate_key,
                            content_type="image/webp",
                        ),
                        timeout=30.0,
                    )
                    thumbnail_key = thumbnail_candidate_key
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

            # ── 7. Persist results ────────────────────────────────────────────
            update_kwargs: dict[str, Any] = {
                "processing_status": "complete",
                "thumbnail_status": thumbnail_status,
            }
            if thumbnail_key:
                update_kwargs["thumbnail_key"] = thumbnail_key

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

        await _trigger_pending_auto_merges(worker_ctx, cas_s3_key)

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
            settled = False
            async with _post_scan_lifecycle_guard(worker_ctx, upload_id):
                settled = await _handle_permanent_failure(
                    repo,
                    exc=exc,
                    attempts=job_try,
                    payload={
                        "upload_id": upload_id,
                        "user_id": user_id,
                        "quarantine_key": quarantine_key,
                        "original_filename": original_filename,
                        "mime_type": mime_type,
                        "original_sha256": original_sha256,
                        "cas_key": cas_key,
                        "cas_s3_key": cas_s3_key,
                        "initial_size": initial_size,
                    },
                )
            if settled:
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
    *,
    exc: Exception,
    attempts: int,
    payload: dict[str, str | int],
) -> bool:
    """Mark the upload degraded unless authoritative cancellation already won."""
    upload_id = str(payload["upload_id"])
    if not await repo.update_processing_status(upload_id, "degraded"):
        return False
    await repo.insert_dead_letter(
        upload_id,
        job_name="process_upload_post_scan",
        payload=payload,
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
    """Auto-merge only when every claimed CAS key is authoritatively eligible.

    Upload lifecycle locks are acquired in stable upload-id order before database
    row locks. This serializes approval with cancellation and retroactive malware
    quarantine for every upload referenced by the contribution.
    """
    if ctx.db_sessionmaker is None:
        return

    try:
        async with ctx.db_sessionmaker() as discovery_db:
            candidate_pr = await discovery_db.scalar(
                select(PullRequest)
                .join(PRFileClaim, PRFileClaim.pr_id == PullRequest.id)
                .where(
                    PRFileClaim.file_key == cas_s3_key,
                    PullRequest.auto_merge_pending.is_(True),
                    PullRequest.status == PRStatus.OPEN,
                )
            )
            if candidate_pr is None:
                return
            candidate_pr_id = candidate_pr.id
            candidate_author_id = candidate_pr.author_id
            candidate_keys = {
                key for key in get_pr_all_file_keys(candidate_pr) if key.startswith("cas/")
            }
            upload_ids = sorted(
                set(
                    await discovery_db.scalars(
                        select(Upload.upload_id).where(
                            Upload.final_key.in_(candidate_keys),
                            Upload.user_id == candidate_author_id,
                        )
                    )
                )
            )

        async with contextlib.AsyncExitStack() as lifecycle_locks:
            for upload_id in upload_ids:
                await lifecycle_locks.enter_async_context(
                    _post_scan_lifecycle_guard(ctx, upload_id)
                )

            async with ctx.db_sessionmaker() as db:
                db.info[PostCommitKey.MANAGED_TRANSACTION] = True
                pr = await db.scalar(
                    select(PullRequest)
                    .where(
                        PullRequest.id == candidate_pr_id,
                        PullRequest.auto_merge_pending.is_(True),
                        PullRequest.status == PRStatus.OPEN,
                    )
                    .with_for_update()
                )
                if pr is None:
                    return

                expected_keys = {key for key in get_pr_all_file_keys(pr) if key.startswith("cas/")}
                if not expected_keys or cas_s3_key not in expected_keys:
                    return
                try:
                    await _lock_and_validate_pr_cas_files(
                        db, pr, settled_statuses=_SETTLED_STATUSES
                    )
                except ConflictError:
                    return

                pr.status = PRStatus.APPROVED
                pr.reviewed_by = pr.author_id
                pr.auto_merge_pending = False

                jobs: list[Any] = []
                db.info[PostCommitKey.JOBS] = jobs
                db.info.setdefault(PostCommitKey.JOB_KEYS, set())

                commit_attempted = False
                try:
                    await apply_pr(db, pr)
                    await _cleanup_pr_resources(db, pr, redis=ctx.redis)  # type: ignore[arg-type]

                    sse_broadcasts = db.info.pop(PostCommitKey.SSE, [])
                    event = {"type": "pr_closed", "id": str(pr.id)}
                    for topic in _pr_directory_topics(list(pr.payload)):
                        sse_broadcasts.append((topic, event))
                    if pr.author_id:
                        sse_broadcasts.append((f"pr_updates:{pr.author_id}", event))

                    await persist_post_commit_jobs(db)
                    commit_attempted = True
                    await db.commit()
                except BaseException:
                    _result, rollback_error, rollback_cancellation = await settle_awaitable(
                        db.rollback()
                    )
                    compensation_error: BaseException | None = None
                    if commit_attempted:
                        # COMMIT acknowledgement is ambiguous after a connection
                        # failure or cancellation. The transaction may be durable,
                        # so destructive compensation could remove referenced data.
                        db.info.pop(PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS, None)
                        db.info.pop(PostCommitKey.TRANSACTION_COMMIT_CALLBACKS, None)
                        logger.error(
                            "Auto-merge COMMIT failed with unknown outcome; preserving "
                            "external mutations"
                        )
                    else:
                        try:
                            await rollback_transaction_callbacks(db)
                        except BaseException as exc:
                            compensation_error = exc
                    if compensation_error is not None and not isinstance(
                        compensation_error, asyncio.CancelledError
                    ):
                        raise RuntimeError(
                            "Auto-merge transaction failed and external-resource "
                            "compensation was incomplete"
                        ) from compensation_error
                    if rollback_error is not None:
                        raise RuntimeError(
                            "Auto-merge database rollback failed"
                        ) from rollback_error
                    if rollback_cancellation is not None:
                        raise rollback_cancellation
                    if compensation_error is not None:
                        raise compensation_error
                    raise
                else:
                    await finalize_transaction_callbacks(db)
                finally:
                    db.info.pop(PostCommitKey.MANAGED_TRANSACTION, None)

                for topic, event in sse_broadcasts:
                    broadcast_to_topic(topic, event)

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
                        await persist_post_commit_jobs(notify_db)
                        await notify_db.commit()
                        await dispatch_post_commit_actions(notify_db)

                logger.info("Auto-merged PR %s after all uploads settled.", pr.id)

    except Exception:
        logger.exception("Failed to trigger auto-merge for cas_s3_key=%s", cas_s3_key)
        raise
