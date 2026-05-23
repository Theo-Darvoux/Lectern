"""Background post-scan processing: compress, thumbnail, and update CAS.

Called by process_upload after the scan gate passes.  The file is already in
CAS (uncompressed, no thumbnail) and the upload row is status=clean,
processing_status=pending.

Failure behaviour (Option A + B):
  - Compression or thumbnail failures are soft: file stays accessible uncompressed.
  - CAS overwrite failures trigger arq retries (up to _POST_MAX_RETRIES attempts).
  - After max retries: processing_status=degraded, dead-letter record, admin alert.
  - A settled state (complete | degraded) triggers pending auto-merge PRs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.core.metrics import mime_category as _mime_cat
from app.core.metrics import upload_compression_ratio, upload_file_size
from app.core.storage import delete_object, upload_file_multipart
from app.core.telemetry import get_tracer
from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
from app.models.upload import Upload
from app.services.notification import notify_user
from app.services.pr import (
    _cleanup_pr_resources,
    _pr_directory_topics,
    apply_pr,
    get_pr_all_file_keys,
)
from app.workers.upload.constants import _compression_timeout
from app.workers.upload.context import WorkerContext
from app.workers.upload.repository import UploadWorkerRepository
from app.workers.upload.stages.compress import run_compress_stage
from app.workers.upload.stages.download import run_download_and_validate
from app.workers.upload.stages.scan_strip import run_strip_only
from app.workers.upload.stages.thumbnail import run_thumbnail_stage

logger = logging.getLogger("wikint")

_POST_MAX_RETRIES = 3
# Settled processing statuses — a PR can auto-merge when all its files reach one of these.
_SETTLED_STATUSES = frozenset({"complete", "degraded"})


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
    """Compress, thumbnail, and overwrite the CAS object for a previously scanned upload.

    The quarantine object is re-downloaded, stripped (idempotent), compressed, and
    thumbnailed.  The resulting file overwrites the uncompressed placeholder uploaded
    by the scan job.  The quarantine object is deleted on success.

    On soft failure (compress or thumbnail), we proceed with the original and mark
    the upload degraded.  On hard failure (CAS upload), arq retries the whole job.
    """
    worker_ctx = WorkerContext.from_arq_ctx(ctx)
    repo = UploadWorkerRepository(worker_ctx)
    tracer = get_tracer()
    job_try: int = ctx.get("job_try", 1)

    await repo.update_processing_status(upload_id, "running")

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
                "file remains uncompressed (already in CAS from scan job).",
                upload_id,
                exc,
            )
            await repo.update_processing_status(upload_id, "degraded")
            await _trigger_pending_auto_merges(worker_ctx, cas_s3_key)
            return

        pf = download_result.pf
        actual_mime = download_result.actual_mime
        auth_config = await repo.get_auth_config()

        # ── 2. Re-strip metadata (idempotent) ────────────────────────────────
        try:
            await run_strip_only(pf, tmp_path, actual_mime, upload_id, tracer)
        except Exception as exc:
            logger.warning(
                "Post-scan metadata strip failed for upload %s: %s — continuing.", upload_id, exc
            )

        # ── 3. Compress (soft failure — degrade gracefully) ──────────────────
        final_mime = actual_mime
        content_encoding: str | None = None
        compress_ok = False
        try:
            comp_timeout = _compression_timeout(actual_mime)
            comp_heartbeat = asyncio.create_task(_compress_heartbeat(comp_timeout))
            try:
                comp_res = await run_compress_stage(
                    pf, actual_mime, original_filename, tracer, config=auth_config
                )
            finally:
                comp_heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await comp_heartbeat
            final_mime = comp_res.final_mime
            content_encoding = comp_res.content_encoding
            compress_ok = True
        except Exception as exc:
            logger.warning(
                "Post-scan compression failed for upload %s: %s — serving uncompressed original.",
                upload_id,
                exc,
            )

        # ── 4. Generate thumbnail (soft failure — no thumbnail is acceptable) ─
        try:
            thumbnail_path = await run_thumbnail_stage(
                pf, final_mime, original_filename, tracer, config=auth_config
            )
        except Exception as exc:
            logger.warning(
                "Post-scan thumbnail generation failed for upload %s: %s — no thumbnail.",
                upload_id,
                exc,
            )

        # ── 5. Overwrite CAS object with compressed file (may raise → retry) ─
        content_sha256 = await pf.sha256()
        await asyncio.wait_for(
            upload_file_multipart(
                pf.path,
                cas_s3_key,
                content_type=final_mime,
                content_encoding=content_encoding,
            ),
            timeout=120.0,
        )

        # ── 6. Upload thumbnail ───────────────────────────────────────────────
        thumbnail_key: str | None = None
        if thumbnail_path:
            cas_id = cas_s3_key.split("/", 1)[-1]
            thumbnail_key = f"thumbnails/{cas_id}.webp"
            try:
                await asyncio.wait_for(
                    upload_file_multipart(
                        Path(thumbnail_path),
                        thumbnail_key,
                        content_type="image/webp",
                    ),
                    timeout=30.0,
                )
            except Exception as exc:
                logger.warning(
                    "Thumbnail upload failed for upload %s: %s — skipping thumbnail.",
                    upload_id,
                    exc,
                )
                thumbnail_key = None
            finally:
                with contextlib.suppress(Exception):
                    Path(thumbnail_path).unlink(missing_ok=True)
                thumbnail_path = None  # already cleaned up

        # ── 7. Persist results ────────────────────────────────────────────────
        update_kwargs: dict[str, Any] = {
            "content_sha256": content_sha256,
            "processing_status": "complete",
            "size_bytes": pf.size,
        }
        if thumbnail_key:
            update_kwargs["thumbnail_key"] = thumbnail_key
        if final_mime != mime_type:
            update_kwargs["mime_type"] = final_mime

        await repo.update_upload_status(upload_id, "clean", **update_kwargs)

        # Backfill MaterialVersion.file_size for any PRs merged before compression
        # finished (manual approvals where apply_pr ran while size_bytes was still
        # the pre-compression value).
        if worker_ctx.db_sessionmaker is not None:
            try:
                from sqlalchemy import update as sa_update

                from app.models.material import MaterialVersion

                async with worker_ctx.db_sessionmaker() as db:
                    await db.execute(
                        sa_update(MaterialVersion)
                        .where(MaterialVersion.file_key == cas_s3_key)
                        .values(file_size=pf.size)
                    )
                    await db.commit()
            except Exception as exc:
                logger.warning(
                    "Failed to backfill MaterialVersion.file_size for %s: %s",
                    cas_s3_key,
                    exc,
                )

        # Update Redis status cache so frontend sees the compressed size
        from app.schemas.material import UploadStatus
        from app.workers.upload.cache_repo import UploadCacheRepository
        from app.workers.upload.constants import _STATUS_CACHE_PREFIX

        cache = UploadCacheRepository(worker_ctx.redis)
        status_key = f"{_STATUS_CACHE_PREFIX}{quarantine_key}"
        event_channel = f"upload:events:{quarantine_key}"
        event_log_key = f"upload:eventlog:{quarantine_key}"

        cached_json = await worker_ctx.redis.get(status_key)
        if cached_json:
            try:
                payload = json.loads(cached_json)
                if payload.get("result"):
                    payload["result"]["size"] = pf.size
                    payload["result"]["processing_status"] = "complete"
                    # If the file was compressed, it's now "correct" to say it's ready
                    payload["status"] = UploadStatus.CLEAN
                    payload["detail"] = "Processing complete"

                    # Also update progress to 100% (finalizing stage index is 4, 1.0)
                    # Actually _overall(4, 1.0) is 100.
                    payload["stage_index"] = 4
                    payload["stage_percent"] = 1.0
                    payload["overall_percent"] = 100

                    new_payload_json = json.dumps(payload)
                    await cache.emit_event(
                        status_key, event_channel, event_log_key, new_payload_json
                    )
            except Exception as exc:
                logger.warning("Failed to update status cache for %s: %s", upload_id, exc)

        # ── 8. Delete quarantine object ───────────────────────────────────────
        try:
            await delete_object(quarantine_key)
        except Exception as exc:
            logger.warning("Failed to delete quarantine %s: %s", quarantine_key, exc)

        # ── 9. Dispatch webhook ───────────────────────────────────────────────
        await repo.maybe_dispatch_webhook(upload_id)

        # ── 10. Prometheus metrics ────────────────────────────────────────────
        mime_cat = _mime_cat(final_mime)
        upload_file_size.labels(mime_category=mime_cat).observe(initial_size)
        if compress_ok and initial_size > 0 and pf.size > 0:
            upload_compression_ratio.labels(mime_category=mime_cat).observe(initial_size / pf.size)

        logger.info(
            "Post-scan processing complete for upload %s (compressed=%s, thumbnail=%s).",
            upload_id,
            compress_ok,
            thumbnail_key is not None,
        )

    except Exception as exc:
        # Hard failure — will be retried by arq unless we've hit the limit.
        logger.exception(
            "Post-scan processing failed for upload %s (attempt %d)", upload_id, job_try
        )

        if job_try >= _POST_MAX_RETRIES:
            await _handle_permanent_failure(repo, upload_id, cas_s3_key, exc, job_try)
            await _trigger_pending_auto_merges(worker_ctx, cas_s3_key)
        else:
            # Reset to pending so status reflects "not yet settled" during retry wait.
            await repo.update_processing_status(upload_id, "pending")
            raise  # Let arq schedule the retry.

    else:
        # Success — check for PRs waiting on this upload.
        await _trigger_pending_auto_merges(worker_ctx, cas_s3_key)

    finally:
        if pf is not None:
            pf.cleanup()
        elif tmp_path is not None:
            with contextlib.suppress(Exception):
                tmp_path.unlink(missing_ok=True)
        if thumbnail_path:
            with contextlib.suppress(Exception):
                Path(thumbnail_path).unlink(missing_ok=True)


async def _compress_heartbeat(max_duration: float) -> None:
    """Yield control periodically so the event loop stays responsive during compression."""
    elapsed = 0.0
    interval = 5.0
    while elapsed < max_duration:
        await asyncio.sleep(interval)
        elapsed += interval


async def _handle_permanent_failure(
    repo: UploadWorkerRepository,
    upload_id: str,
    cas_s3_key: str,
    exc: Exception,
    attempts: int,
) -> None:
    """Mark the upload degraded and insert a dead-letter record after max retries."""
    await repo.update_processing_status(upload_id, "degraded")
    await repo.insert_dead_letter(
        upload_id,
        job_name="process_upload_post_scan",
        payload={"upload_id": upload_id, "cas_s3_key": cas_s3_key},
        error=str(exc),
        attempts=attempts,
    )
    logger.error(
        "Post-scan processing permanently failed for upload %s after %d attempts — "
        "serving uncompressed original.  Dead-letter record inserted.",
        upload_id,
        attempts,
    )


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
            from sqlalchemy import func, select

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
            db.info["post_commit_jobs"] = jobs
            db.info.setdefault("post_commit_job_keys", set())

            await apply_pr(db, pr)
            await _cleanup_pr_resources(db, pr, redis=ctx.redis)  # type: ignore[arg-type]

            sse_broadcasts = db.info.pop("post_commit_sse_broadcasts", [])
            event = {"type": "pr_closed", "id": str(pr.id)}
            for topic in _pr_directory_topics(list(pr.payload)):
                sse_broadcasts.append((topic, event))
            if pr.author_id:
                sse_broadcasts.append((f"pr_updates:{pr.author_id}", event))

            await db.commit()

            from app.core.sse import broadcast_to_topic

            for topic, event in sse_broadcasts:
                broadcast_to_topic(topic, event)

            # Dispatch arq jobs queued inside apply_pr (index_material, etc.)
            await _dispatch_post_commit_jobs(jobs)

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


async def _dispatch_post_commit_jobs(jobs: list[Any]) -> None:
    """Enqueue arq jobs accumulated in db.info['post_commit_jobs'] during apply_pr."""
    if not jobs:
        return

    import app.core.redis as redis_core
    from app.core.database import _coalesce_index_jobs

    coalesced = _coalesce_index_jobs(jobs)
    if redis_core.arq_pool is None:
        logger.error(
            "arq_pool unavailable — %d post-commit jobs from auto-merge lost.", len(coalesced)
        )
        return

    for job in coalesced:
        try:
            await redis_core.arq_pool.enqueue_job(*job)
        except Exception as exc:
            logger.error("Failed to enqueue post-commit job %s after auto-merge: %s", job, exc)
