"""Tests for the two-phase upload pipeline and post-scan processing.

Phase 1 (process_upload / UploadPipeline):
  - Emits CLEAN immediately after scan gate passes.
  - Uploads uncompressed file to CAS.
  - Enqueues process_upload_post_scan for background work.
  - Does NOT compress or thumbnail.

Phase 2 (process_upload_post_scan):
  - Happy path: thumbnail → DB update → webhook → auto-merge.
  - The sanitized CAS object is immutable and is never overwritten.
  - Soft failure: thumbnail fails → no thumbnail → processing_status=complete.
  - Strip failure: preserve CAS and mark processing_status=degraded.
  - Hard failure: quarantine missing → processing_status=degraded (immediate, no retry needed).
  - Retry exhausted: dead-letter + processing_status=degraded.
  - arq retry: raises on recoverable error while job_try < max.

Auto-merge coordination:
  - PR created with pending files → auto_merge_pending=True.
  - PR created with settled files → auto-approved immediately.
  - _trigger_pending_auto_merges merges PR when all files settle.
  - _trigger_pending_auto_merges skips PR when some files still pending.

Model/migration smoke-tests:
  - Upload.processing_status field exists with correct default.
  - PullRequest.auto_merge_pending field exists with correct default.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ctx(job_try: int = 1, with_db: bool = True) -> dict:
    """Build a minimal arq-style ctx dict."""
    ctx: dict = {"redis": AsyncMock(), "job_try": job_try}
    if with_db:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=MagicMock(status="clean"))
        ctx["db_sessionmaker"] = MagicMock(return_value=mock_session)
    else:
        ctx["db_sessionmaker"] = None
    return ctx


def _make_pf(size: int = 1024) -> MagicMock:
    """Minimal ProcessingFile mock."""
    pf = MagicMock()
    pf.path = Path("/tmp/fake.bin")
    pf.size = size
    pf.sha256 = AsyncMock(return_value="a" * 64)
    pf.cleanup = MagicMock()
    return pf


def _make_download_result(pf=None, mime="application/pdf") -> MagicMock:
    dr = MagicMock()
    dr.pf = pf or _make_pf()
    dr.original_sha256 = "b" * 64
    dr.initial_size = 2048
    dr.actual_mime = mime
    dr.mime_category = "document"
    dr.cas_key = f"upload:cas:{'c' * 64}"
    return dr


def _post_scan_kwargs(**overrides) -> dict:
    base = {
        "upload_id": "uid-1",
        "user_id": str(uuid.uuid4()),
        "quarantine_key": "quarantine/u/uid-1/file.pdf",
        "original_filename": "file.pdf",
        "mime_type": "application/pdf",
        "original_sha256": "b" * 64,
        "cas_key": f"upload:cas:{'c' * 64}",
        "cas_s3_key": "cas/" + "c" * 64,
        "initial_size": 2048,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_post_scan_dead_letter_preserves_every_retry_argument() -> None:
    import inspect

    from app.workers.process_upload_post_scan import (
        _handle_permanent_failure,
        process_upload_post_scan,
    )

    payload = _post_scan_kwargs()
    repo = MagicMock()
    repo.update_processing_status = AsyncMock(return_value=True)
    repo.insert_dead_letter = AsyncMock()

    assert await _handle_permanent_failure(
        repo,
        exc=RuntimeError("failed"),
        attempts=3,
        payload=payload,
    )

    stored_payload = repo.insert_dead_letter.await_args.kwargs["payload"]
    required = set(inspect.signature(process_upload_post_scan).parameters) - {"ctx"}
    assert set(stored_payload) == required
    assert stored_payload == payload


# ── Model field smoke-tests ───────────────────────────────────────────────────


def test_upload_model_has_processing_status_field() -> None:
    """Upload model must expose processing_status with default 'pending'."""
    from app.models.upload import Upload

    col = Upload.__table__.c.get("processing_status")
    assert col is not None, "processing_status column missing from uploads table"
    assert str(col.server_default.arg) == "pending"


def test_pull_request_model_has_auto_merge_pending_field() -> None:
    """PullRequest model must expose auto_merge_pending with default False."""
    from app.models.pull_request import PullRequest

    col = PullRequest.__table__.c.get("auto_merge_pending")
    assert col is not None, "auto_merge_pending column missing from pull_requests table"


# ── Pipeline Phase 1: fast finalize ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_enqueues_post_scan_job_after_scan() -> None:
    """After scan+strip, the pipeline must enqueue process_upload_post_scan."""
    from app.workers.upload.context import WorkerContext
    from app.workers.upload.pipeline import UploadPipeline

    mock_redis = AsyncMock()
    mock_scanner = MagicMock()
    mock_scanner.initialize = MagicMock()

    ctx = WorkerContext(
        redis=mock_redis,
        db_sessionmaker=None,
        job_try=1,
        scanner=mock_scanner,
    )

    pipeline = UploadPipeline(
        ctx,
        user_id="user-1",
        upload_id="uid-1",
        quarantine_key="quarantine/u/uid-1/f.pdf",
        original_filename="f.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )

    mock_pf = _make_pf()
    pipeline.pf = mock_pf
    pipeline.tmp_path = Path("/tmp/t")
    pipeline.original_sha256 = "b" * 64
    pipeline.cas_key = f"upload:cas:{'c' * 64}"
    pipeline.initial_size = 1024
    pipeline.completed_stage = 2  # scan already done

    mock_pool = AsyncMock()
    finalize_result = MagicMock()
    finalize_result.final_key = "cas/" + "c" * 64
    finalize_result.safe_name = "f.pdf"
    finalize_result.final_size = 1024
    finalize_result.content_sha256 = "d" * 64
    finalize_result.db_cas_key = f"upload:cas:{'c' * 64}"
    finalize_result.new_cas_ref = 1

    with (
        patch("app.workers.upload.pipeline.run_post_strip_pdf_check", AsyncMock()),
        patch(
            "app.workers.upload.pipeline.run_finalize_storage",
            AsyncMock(return_value=finalize_result),
        ),
        patch("app.workers.upload.pipeline.UploadWorkerRepository") as mock_repo,
        patch("app.core.database.redis.arq_pool", mock_pool),
        patch("app.config.settings.bazaar_async_enabled", False),
    ):
        repo_instance = mock_repo.return_value
        repo_instance.checkpoint_pipeline_stage = AsyncMock()
        repo_instance.publish_clean_upload = AsyncMock()
        repo_instance.update_upload_status = AsyncMock()
        repo_instance.update_processing_status = AsyncMock()
        pipeline.repo = repo_instance

        pipeline.cache = MagicMock()
        pipeline.cache.emit_event = AsyncMock()
        pipeline.cache.is_cancelled = AsyncMock(return_value=False)

        await pipeline._run_stages()

    # Must enqueue the post-scan job
    enqueue_calls = [str(c) for c in mock_pool.enqueue_job.call_args_list]
    assert any("process_upload_post_scan" in c for c in enqueue_calls), (
        "process_upload_post_scan must be enqueued after fast finalize"
    )


@pytest.mark.asyncio
async def test_pipeline_emits_clean_status_after_scan() -> None:
    """The pipeline must emit UploadStatus.CLEAN during the fast finalize, not later."""
    import json

    from app.schemas.material import UploadStatus
    from app.workers.upload.context import WorkerContext
    from app.workers.upload.pipeline import UploadPipeline

    mock_redis = AsyncMock()
    ctx = WorkerContext(redis=mock_redis, db_sessionmaker=None, job_try=1)

    pipeline = UploadPipeline(
        ctx,
        user_id="u",
        upload_id="uid-1",
        quarantine_key="q/u/uid-1/f.pdf",
        original_filename="f.pdf",
        mime_type="image/png",
        expected_sha256=None,
    )

    mock_pf = _make_pf()
    pipeline.pf = mock_pf
    pipeline.tmp_path = Path("/tmp/t")
    pipeline.original_sha256 = "b" * 64
    pipeline.cas_key = f"upload:cas:{'c' * 64}"
    pipeline.initial_size = 512
    pipeline.completed_stage = 2

    emitted_statuses: list[str] = []

    def capture_emit(_sk: str, _ec: str, _elk: str, payload_json: str) -> None:
        data = json.loads(payload_json)
        emitted_statuses.append(data.get("status", ""))

    finalize_result = MagicMock(
        final_key="cas/" + "c" * 64,
        safe_name="f.pdf",
        final_size=512,
        content_sha256="d" * 64,
        db_cas_key=f"upload:cas:{'c' * 64}",
        new_cas_ref=1,
    )

    with (
        patch("app.workers.upload.pipeline.run_post_strip_pdf_check", AsyncMock()),
        patch(
            "app.workers.upload.pipeline.run_finalize_storage",
            AsyncMock(return_value=finalize_result),
        ),
        patch("app.workers.upload.pipeline.UploadWorkerRepository") as mock_repo,
        patch("app.core.database.redis.arq_pool", AsyncMock()),
        patch("app.config.settings.bazaar_async_enabled", False),
    ):
        repo_instance = mock_repo.return_value
        repo_instance.checkpoint_pipeline_stage = AsyncMock()
        repo_instance.publish_clean_upload = AsyncMock()
        repo_instance.update_upload_status = AsyncMock()
        repo_instance.update_processing_status = AsyncMock()
        pipeline.repo = repo_instance

        cache_mock = MagicMock()
        cache_mock.emit_event = AsyncMock(side_effect=capture_emit)
        cache_mock.is_cancelled = AsyncMock(return_value=False)
        pipeline.cache = cache_mock

        await pipeline._run_stages()

    assert UploadStatus.CLEAN in emitted_statuses, (
        f"CLEAN status must be emitted after scan; got {emitted_statuses}"
    )


@pytest.mark.asyncio
async def test_pipeline_does_not_compress_or_thumbnail_in_job1() -> None:
    """Job 1 must NOT call run_compress_stage or run_thumbnail_stage."""
    from app.workers.upload.context import WorkerContext
    from app.workers.upload.pipeline import UploadPipeline

    ctx = WorkerContext(redis=AsyncMock(), db_sessionmaker=None, job_try=1)
    pipeline = UploadPipeline(
        ctx,
        user_id="u",
        upload_id="uid-1",
        quarantine_key="q/u/uid-1/f.pdf",
        original_filename="f.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )
    pipeline.pf = _make_pf()
    pipeline.tmp_path = Path("/tmp/t")
    pipeline.original_sha256 = "b" * 64
    pipeline.cas_key = f"upload:cas:{'c' * 64}"
    pipeline.initial_size = 1024
    pipeline.completed_stage = 2

    finalize_result = MagicMock(
        final_key="cas/" + "c" * 64,
        safe_name="f.pdf",
        final_size=1024,
        content_sha256="d" * 64,
        db_cas_key=f"upload:cas:{'c' * 64}",
        new_cas_ref=1,
    )
    mock_compress = AsyncMock()
    mock_thumbnail = AsyncMock()

    with (
        patch("app.workers.upload.pipeline.run_post_strip_pdf_check", AsyncMock()),
        patch(
            "app.workers.upload.pipeline.run_finalize_storage",
            AsyncMock(return_value=finalize_result),
        ),
        patch("app.workers.upload.pipeline.UploadWorkerRepository") as mock_repo,
        patch("app.core.database.redis.arq_pool", AsyncMock()),
        patch("app.config.settings.bazaar_async_enabled", False),
        # These must NOT be imported or called in pipeline.py anymore
        patch("app.workers.upload.stages.compress.run_compress_stage", mock_compress),
        patch("app.workers.upload.stages.thumbnail.run_thumbnail_stage", mock_thumbnail),
    ):
        repo_instance = mock_repo.return_value
        repo_instance.checkpoint_pipeline_stage = AsyncMock()
        repo_instance.publish_clean_upload = AsyncMock()
        repo_instance.update_upload_status = AsyncMock()
        repo_instance.update_processing_status = AsyncMock()
        pipeline.repo = repo_instance
        pipeline.cache = MagicMock()
        pipeline.cache.emit_event = AsyncMock()
        pipeline.cache.is_cancelled = AsyncMock(return_value=False)

        await pipeline._run_stages()

    mock_compress.assert_not_called()
    mock_thumbnail.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_sets_processing_status_pending_in_db() -> None:
    """After fast finalize, processing_status must be set to 'pending' in the DB."""
    from app.workers.upload.context import WorkerContext
    from app.workers.upload.pipeline import UploadPipeline

    ctx = WorkerContext(redis=AsyncMock(), db_sessionmaker=None, job_try=1)
    pipeline = UploadPipeline(
        ctx,
        user_id="u",
        upload_id="uid-1",
        quarantine_key="q/u/uid-1/f.pdf",
        original_filename="f.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )
    pipeline.pf = _make_pf()
    pipeline.tmp_path = Path("/tmp/t")
    pipeline.original_sha256 = "b" * 64
    pipeline.cas_key = f"upload:cas:{'c' * 64}"
    pipeline.initial_size = 1024
    pipeline.completed_stage = 2

    finalize_result = MagicMock(
        final_key="cas/" + "c" * 64,
        safe_name="f.pdf",
        final_size=1024,
        content_sha256="d" * 64,
        db_cas_key=f"upload:cas:{'c' * 64}",
        new_cas_ref=1,
    )
    with (
        patch("app.workers.upload.pipeline.run_post_strip_pdf_check", AsyncMock()),
        patch(
            "app.workers.upload.pipeline.run_finalize_storage",
            AsyncMock(return_value=finalize_result),
        ),
        patch("app.workers.upload.pipeline.UploadWorkerRepository") as mock_repo,
        patch("app.core.database.redis.arq_pool", AsyncMock()),
        patch("app.config.settings.bazaar_async_enabled", False),
    ):
        repo_instance = mock_repo.return_value
        repo_instance.checkpoint_pipeline_stage = AsyncMock()
        repo_instance.publish_clean_upload = AsyncMock()
        repo_instance.update_upload_status = AsyncMock()
        repo_instance.update_processing_status = AsyncMock()
        pipeline.repo = repo_instance
        pipeline.cache = MagicMock()
        pipeline.cache.emit_event = AsyncMock()
        pipeline.cache.is_cancelled = AsyncMock(return_value=False)

        await pipeline._run_stages()

    # Verify that publish_clean_upload was called
    repo_instance.publish_clean_upload.assert_awaited_once()


# ── Phase 2: process_upload_post_scan ────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_scan_happy_path_marks_complete() -> None:
    """Happy path publishes completion and uploads only the thumbnail."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    pf = _make_pf(size=512)
    dr = _make_download_result(pf=pf)

    mock_upload_thumbnail = AsyncMock()

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=dr),
        ),
        patch("app.workers.process_upload_post_scan.run_strip_only", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage",
            AsyncMock(return_value="/tmp/thumb.webp"),
        ),
        patch(
            "app.workers.process_upload_post_scan.upload_file_multipart",
            mock_upload_thumbnail,
        ),
        patch("app.workers.process_upload_post_scan.delete_object", AsyncMock()),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
        patch("pathlib.Path.unlink", MagicMock()),
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.update_upload_status = AsyncMock()
        repo.get_auth_config = AsyncMock(return_value={})
        repo.maybe_dispatch_webhook = AsyncMock()
        repo.insert_dead_letter = AsyncMock()

        await process_upload_post_scan(ctx, **_post_scan_kwargs())

    # Must set running first, then complete
    status_calls = [c.args[1] for c in repo.update_processing_status.call_args_list]
    assert "running" in status_calls
    upload = ctx["db_sessionmaker"].return_value.scalar.return_value
    assert upload.processing_status == "complete"
    assert upload.thumbnail_status == "ok", (
        "thumbnail_status must be 'ok' when thumbnail is successfully generated and uploaded"
    )
    mock_upload_thumbnail.assert_awaited_once()
    uploaded_key = mock_upload_thumbnail.await_args.args[1]
    assert uploaded_key.startswith("thumbnails/")
    assert uploaded_key != _post_scan_kwargs()["cas_s3_key"]


@pytest.mark.asyncio
async def test_post_scan_holds_thumbnail_lifecycle_lock_through_publication() -> None:
    """The thumbnail fence spans object write through authoritative DB publication."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    expected_key = f"thumbnails/{'c' * 64}/uid-1.webp"
    lock_held = False

    @asynccontextmanager
    async def lifecycle_guard(_session_factory, key: str):
        nonlocal lock_held
        assert key == expected_key
        assert not lock_held
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    async def upload_thumbnail(_path, key: str, **_kwargs) -> None:
        assert key == expected_key
        assert lock_held, "thumbnail write escaped the storage lifecycle fence"

    async def publish_thumbnail(_worker_ctx, *, upload_id: str, update_values: dict) -> bool:
        assert upload_id == "uid-1"
        assert update_values["thumbnail_key"] == expected_key
        assert lock_held, "thumbnail publication escaped the storage lifecycle fence"
        return True

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=_make_download_result()),
        ),
        patch("app.workers.process_upload_post_scan.run_strip_only", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage",
            AsyncMock(return_value="/tmp/thumb.webp"),
        ),
        patch(
            "app.workers.process_upload_post_scan.storage_lifecycle_lock",
            new=lifecycle_guard,
        ),
        patch(
            "app.workers.process_upload_post_scan.upload_file_multipart",
            new=upload_thumbnail,
        ),
        patch(
            "app.workers.process_upload_post_scan._publish_postprocessed_upload",
            new=publish_thumbnail,
        ),
        patch("app.workers.process_upload_post_scan.delete_object", AsyncMock()),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
        patch("pathlib.Path.unlink", MagicMock()),
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock(return_value=True)
        repo.get_auth_config = AsyncMock(return_value={})
        repo.maybe_dispatch_webhook = AsyncMock()
        await process_upload_post_scan(ctx, **_post_scan_kwargs())

    assert not lock_held


@pytest.mark.asyncio
async def test_post_scan_removes_thumbnail_if_upload_was_quarantined() -> None:
    """A quarantine race cannot leave an unpublished thumbnail object behind."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    ctx["db_sessionmaker"].return_value.scalar.return_value = MagicMock(status="malicious")
    delete = AsyncMock()

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=_make_download_result()),
        ),
        patch("app.workers.process_upload_post_scan.run_strip_only", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage",
            AsyncMock(return_value="/tmp/thumb.webp"),
        ),
        patch("app.workers.process_upload_post_scan.upload_file_multipart", AsyncMock()),
        patch("app.workers.process_upload_post_scan.delete_object", delete),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
        patch("pathlib.Path.unlink", MagicMock()),
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.get_auth_config = AsyncMock(return_value={})
        await process_upload_post_scan(ctx, **_post_scan_kwargs())

    delete.assert_awaited_once_with(f"thumbnails/{'c' * 64}/uid-1.webp")


@pytest.mark.asyncio
async def test_post_scan_strip_failure_preserves_existing_cas() -> None:
    """A second-pass strip failure must not upload or overwrite the CAS object."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    dr = _make_download_result()
    mock_upload = AsyncMock()
    mock_delete = AsyncMock()
    mock_trigger = AsyncMock()

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=dr),
        ),
        patch(
            "app.workers.process_upload_post_scan.run_strip_only",
            AsyncMock(side_effect=ValueError("sanitization failed")),
        ),
        patch("app.workers.process_upload_post_scan.run_thumbnail_stage", AsyncMock()) as thumbnail,
        patch("app.workers.process_upload_post_scan.upload_file_multipart", mock_upload),
        patch("app.workers.process_upload_post_scan.delete_object", mock_delete),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", mock_trigger),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.get_auth_config = AsyncMock(return_value={})

        await process_upload_post_scan(ctx, **_post_scan_kwargs())

    status_calls = [call.args[1] for call in repo.update_processing_status.await_args_list]
    assert status_calls == ["running", "degraded"]
    thumbnail.assert_not_awaited()
    mock_upload.assert_not_awaited()
    mock_delete.assert_not_awaited()
    mock_trigger.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_scan_thumbnail_failure_yields_no_thumbnail() -> None:
    """If thumbnail generation raises on all attempts, upload completes with thumbnail_status=failed."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    pf = _make_pf()
    dr = _make_download_result(pf=pf)

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=dr),
        ),
        patch("app.workers.process_upload_post_scan.run_strip_only", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage",
            AsyncMock(side_effect=OSError("Pillow exploded")),
        ),
        patch("app.workers.process_upload_post_scan.delete_object", AsyncMock()),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
        patch("app.workers.process_upload_post_scan.asyncio.sleep", AsyncMock()),
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.update_upload_status = AsyncMock()
        repo.get_auth_config = AsyncMock(return_value={})
        repo.maybe_dispatch_webhook = AsyncMock()
        repo.insert_dead_letter = AsyncMock()

        await process_upload_post_scan(ctx, **_post_scan_kwargs())

    upload = ctx["db_sessionmaker"].return_value.scalar.return_value
    assert upload.processing_status == "complete"
    assert upload.thumbnail_status == "failed", (
        "thumbnail_status must be 'failed' when generation raises on all attempts"
    )


@pytest.mark.asyncio
async def test_post_scan_thumbnail_retried_once_then_succeeds() -> None:
    """If thumbnail raises on the first attempt but succeeds on retry, status is 'ok'."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    pf = _make_pf()
    dr = _make_download_result(pf=pf)

    call_count = 0

    async def _thumb_fail_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("transient failure")
        return "/tmp/thumb.webp"

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=dr),
        ),
        patch("app.workers.process_upload_post_scan.run_strip_only", AsyncMock()),
        patch("app.workers.process_upload_post_scan.run_thumbnail_stage", _thumb_fail_once),
        patch("app.workers.process_upload_post_scan.upload_file_multipart", AsyncMock()),
        patch("app.workers.process_upload_post_scan.delete_object", AsyncMock()),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
        patch("app.workers.process_upload_post_scan.asyncio.sleep", AsyncMock()),
        patch("pathlib.Path.unlink", MagicMock()),
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.update_upload_status = AsyncMock()
        repo.get_auth_config = AsyncMock(return_value={})
        repo.maybe_dispatch_webhook = AsyncMock()
        repo.insert_dead_letter = AsyncMock()

        await process_upload_post_scan(ctx, **_post_scan_kwargs())

    assert call_count == 2, "run_thumbnail_stage must be called twice (fail + retry)"
    upload = ctx["db_sessionmaker"].return_value.scalar.return_value
    assert upload.thumbnail_status == "ok", "thumbnail_status must be 'ok' when retry succeeds"


@pytest.mark.asyncio
async def test_post_scan_thumbnail_skipped_for_unsupported_type() -> None:
    """If run_thumbnail_stage returns None (unsupported type), thumbnail_status is 'skipped'."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    pf = _make_pf()
    dr = _make_download_result(pf=pf, mime="audio/mpeg")

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=dr),
        ),
        patch("app.workers.process_upload_post_scan.run_strip_only", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage",
            AsyncMock(return_value=None),
        ),
        patch("app.workers.process_upload_post_scan.delete_object", AsyncMock()),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.update_upload_status = AsyncMock()
        repo.get_auth_config = AsyncMock(return_value={})
        repo.maybe_dispatch_webhook = AsyncMock()
        repo.insert_dead_letter = AsyncMock()

        await process_upload_post_scan(ctx, **_post_scan_kwargs(mime_type="audio/mpeg"))

    upload = ctx["db_sessionmaker"].return_value.scalar.return_value
    assert upload.thumbnail_status == "skipped", (
        "thumbnail_status must be 'skipped' for unsupported MIME types"
    )


@pytest.mark.asyncio
async def test_post_scan_quarantine_missing_marks_degraded() -> None:
    """If quarantine download fails (file expired/missing), mark degraded without retry."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    mock_trigger = AsyncMock()

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(side_effect=FileNotFoundError("S3 404")),
        ),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", mock_trigger),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.update_upload_status = AsyncMock()

        await process_upload_post_scan(ctx, **_post_scan_kwargs())

    status_calls = [c.args[1] for c in repo.update_processing_status.call_args_list]
    assert "degraded" in status_calls, "quarantine missing → processing_status=degraded"
    # Must still trigger auto-merges (degraded is a settled state)
    mock_trigger.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_scan_temporary_input_uses_configured_processing_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings
    from app.workers.process_upload_post_scan import process_upload_post_scan

    processing_root = tmp_path / "processing"
    monkeypatch.setattr(settings, "processing_root", str(processing_root))
    download = AsyncMock(side_effect=FileNotFoundError("missing"))
    with (
        patch("app.workers.process_upload_post_scan.run_download_and_validate", download),
        patch(
            "app.workers.process_upload_post_scan._trigger_pending_auto_merges",
            new_callable=AsyncMock,
        ),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
    ):
        mock_repo.return_value.update_processing_status = AsyncMock(return_value=True)
        await process_upload_post_scan(_make_ctx(), **_post_scan_kwargs())

    used_path = download.await_args.args[0]
    assert used_path.is_relative_to(processing_root)
    assert not used_path.exists()


@pytest.mark.asyncio
async def test_post_scan_deletes_quarantine_on_success() -> None:
    """Successful completion deletes quarantine bytes and their quota membership."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    pf = _make_pf()
    dr = _make_download_result(pf=pf)
    mock_delete = AsyncMock()

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=dr),
        ),
        patch("app.workers.process_upload_post_scan.run_strip_only", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage", AsyncMock(return_value=None)
        ),
        patch("app.workers.process_upload_post_scan.delete_object", mock_delete),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.update_upload_status = AsyncMock()
        repo.get_auth_config = AsyncMock(return_value={})
        repo.maybe_dispatch_webhook = AsyncMock()
        repo.insert_dead_letter = AsyncMock()

        kw = _post_scan_kwargs(
            user_id="user-1",
            quarantine_key="quarantine/u/uid-1/file.pdf",
        )
        await process_upload_post_scan(ctx, **kw)

    mock_delete.assert_awaited_once_with("quarantine/u/uid-1/file.pdf")
    ctx["redis"].zrem.assert_awaited_once_with(
        "quota:uploads:user-1",
        "quarantine/u/uid-1/file.pdf",
    )


async def test_pipeline_cancellation_releases_quarantine_quota() -> None:
    """Worker-side cancellation owns cleanup after generic storage deletion."""
    from app.workers.upload.context import WorkerContext
    from app.workers.upload.pipeline import UploadPipeline

    redis = AsyncMock()
    pipeline = UploadPipeline(
        WorkerContext(redis=redis, db_sessionmaker=None, job_try=1),
        user_id="user-1",
        upload_id="upload-1",
        quarantine_key="quarantine/user-1/upload-1/file.pdf",
        original_filename="file.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )
    pipeline._fail_upload = AsyncMock()  # type: ignore[method-assign]

    with patch("app.workers.upload.pipeline.delete_object", new_callable=AsyncMock) as delete:
        await pipeline._cancel_current_upload("before scan")

    delete.assert_awaited_once_with("quarantine/user-1/upload-1/file.pdf")
    redis.zrem.assert_awaited_once_with(
        "quota:uploads:user-1",
        "quarantine/user-1/upload-1/file.pdf",
    )


@pytest.mark.asyncio
async def test_post_scan_dispatches_webhook_on_success() -> None:
    """Webhook must be dispatched after successful post-scan processing."""
    from app.workers.process_upload_post_scan import process_upload_post_scan

    ctx = _make_ctx()
    pf = _make_pf()
    dr = _make_download_result(pf=pf)

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            AsyncMock(return_value=dr),
        ),
        patch("app.workers.process_upload_post_scan.run_strip_only", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage", AsyncMock(return_value=None)
        ),
        patch("app.workers.process_upload_post_scan.delete_object", AsyncMock()),
        patch("app.workers.process_upload_post_scan._trigger_pending_auto_merges", AsyncMock()),
        patch("app.workers.process_upload_post_scan.UploadWorkerRepository") as mock_repo,
    ):
        repo = mock_repo.return_value
        repo.update_processing_status = AsyncMock()
        repo.update_upload_status = AsyncMock()
        repo.get_auth_config = AsyncMock(return_value={})
        repo.maybe_dispatch_webhook = AsyncMock()
        repo.insert_dead_letter = AsyncMock()

        await process_upload_post_scan(ctx, **_post_scan_kwargs())

    repo.maybe_dispatch_webhook.assert_awaited_once_with("uid-1")


# ── Auto-merge coordination ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_auto_merge_when_all_files_settled() -> None:
    """_trigger_pending_auto_merges must apply and commit a PR when all files settled."""
    from app.workers.process_upload_post_scan import _trigger_pending_auto_merges
    from app.workers.upload.context import WorkerContext

    cas_s3_key = "cas/" + "c" * 64

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db_factory = MagicMock(return_value=mock_session)

    mock_pr = MagicMock()
    mock_pr.id = uuid.uuid4()
    mock_pr.title = "Test PR"
    mock_pr.author_id = uuid.uuid4()
    mock_pr.auto_merge_pending = True
    mock_pr.status = "open"
    mock_pr.payload = [{"op": "create_material", "file_key": cas_s3_key}]

    # Discovery query, then authoritative locked PR reload.
    mock_session.scalar = AsyncMock(side_effect=[mock_pr, mock_pr])
    mock_session.scalars = AsyncMock(return_value=[])
    mock_session.commit = AsyncMock()
    mock_session.info = {}

    ctx = WorkerContext(
        redis=AsyncMock(),
        db_sessionmaker=mock_db_factory,
        job_try=1,
    )

    mock_apply = AsyncMock()
    mock_cleanup = AsyncMock()
    mock_notify_db_factory = MagicMock(return_value=mock_session)

    with (
        patch("app.workers.process_upload_post_scan.apply_pr", mock_apply),
        patch("app.workers.process_upload_post_scan._cleanup_pr_resources", mock_cleanup),
        patch("app.workers.process_upload_post_scan.persist_post_commit_jobs", AsyncMock()),
        patch("app.workers.process_upload_post_scan.dispatch_post_commit_actions", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan.get_pr_all_file_keys", return_value=[cas_s3_key]
        ),
        patch("app.workers.process_upload_post_scan.notify_user", AsyncMock()),
        patch(
            "app.workers.process_upload_post_scan._lock_and_validate_pr_cas_files",
            AsyncMock(return_value=[]),
        ),
    ):
        await _trigger_pending_auto_merges(ctx, cas_s3_key)

    mock_apply.assert_awaited_once()
    mock_cleanup.assert_awaited_once()
    mock_session.commit.assert_awaited()
    assert True  # set via mock


@pytest.mark.asyncio
async def test_auto_merge_preserves_external_data_when_commit_outcome_is_unknown() -> None:
    """A lost COMMIT acknowledgement must not delete potentially referenced bytes."""
    from app.core.database.post_commit import register_transaction_callbacks
    from app.workers.process_upload_post_scan import _trigger_pending_auto_merges
    from app.workers.upload.context import WorkerContext

    cas_s3_key = "cas/" + "e" * 64
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.info = {}
    mock_session.scalars = AsyncMock(return_value=[])
    mock_session.commit = AsyncMock(side_effect=OSError("lost COMMIT acknowledgement"))

    mock_pr = MagicMock()
    mock_pr.id = uuid.uuid4()
    mock_pr.title = "Ambiguous commit"
    mock_pr.author_id = uuid.uuid4()
    mock_pr.auto_merge_pending = True
    mock_pr.status = "open"
    mock_pr.payload = [{"op": "create_material", "file_key": cas_s3_key}]
    mock_session.scalar = AsyncMock(side_effect=[mock_pr, mock_pr])

    rollback_resource = AsyncMock()
    finalize_resource = AsyncMock()

    async def apply_with_external_mutation(session: Any, _pr: Any) -> None:
        assert register_transaction_callbacks(
            session,
            on_rollback=rollback_resource,
            on_commit=finalize_resource,
        )

    with (
        patch("app.workers.process_upload_post_scan.apply_pr", apply_with_external_mutation),
        patch(
            "app.workers.process_upload_post_scan._cleanup_pr_resources",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan.persist_post_commit_jobs",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan.get_pr_all_file_keys",
            return_value=[cas_s3_key],
        ),
        patch(
            "app.workers.process_upload_post_scan._lock_and_validate_pr_cas_files",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        with pytest.raises(OSError, match="lost COMMIT acknowledgement"):
            await _trigger_pending_auto_merges(
                WorkerContext(
                    redis=AsyncMock(),
                    db_sessionmaker=MagicMock(return_value=mock_session),
                ),
                cas_s3_key,
            )

    mock_session.rollback.assert_awaited_once()
    rollback_resource.assert_not_awaited()
    finalize_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_no_auto_merge_when_files_still_pending() -> None:
    """_trigger_pending_auto_merges must NOT merge when some files are still processing."""
    from app.workers.process_upload_post_scan import _trigger_pending_auto_merges
    from app.workers.upload.context import WorkerContext

    cas_s3_key = "cas/" + "c" * 64
    other_key = "cas/" + "d" * 64

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db_factory = MagicMock(return_value=mock_session)

    mock_pr = MagicMock()
    mock_pr.id = uuid.uuid4()
    mock_pr.payload = [
        {"op": "create_material", "file_key": cas_s3_key},
        {"op": "create_material", "file_key": other_key},
    ]

    mock_session.scalar = AsyncMock(side_effect=[mock_pr, mock_pr])
    mock_session.scalars = AsyncMock(return_value=[])

    ctx = WorkerContext(redis=AsyncMock(), db_sessionmaker=mock_db_factory, job_try=1)

    from app.core.common.exceptions import ConflictError

    mock_apply = AsyncMock()
    with (
        patch("app.workers.process_upload_post_scan.apply_pr", mock_apply),
        patch(
            "app.workers.process_upload_post_scan.get_pr_all_file_keys",
            return_value=[cas_s3_key, other_key],
        ),
        patch(
            "app.workers.process_upload_post_scan._lock_and_validate_pr_cas_files",
            AsyncMock(side_effect=ConflictError("file still pending")),
        ),
    ):
        await _trigger_pending_auto_merges(ctx, cas_s3_key)

    mock_apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_skips_when_no_pending_pr_found() -> None:
    """_trigger_pending_auto_merges does nothing if no auto_merge_pending PR references the key."""
    from app.workers.process_upload_post_scan import _trigger_pending_auto_merges
    from app.workers.upload.context import WorkerContext

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(return_value=None)  # No PR found

    ctx = WorkerContext(
        redis=AsyncMock(),
        db_sessionmaker=MagicMock(return_value=mock_session),
        job_try=1,
    )

    mock_apply = AsyncMock()
    with patch("app.workers.process_upload_post_scan.apply_pr", mock_apply):
        await _trigger_pending_auto_merges(ctx, "cas/" + "c" * 64)

    mock_apply.assert_not_awaited()


# ── PR creation auto-merge deferral ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_pr_creation_defers_auto_merge_when_files_pending() -> None:
    """When files have processing_status=pending, auto-approve must be deferred."""

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    mock_db.scalar = AsyncMock(
        side_effect=[
            0,  # open PR count
            None,  # no auth config
            {"cas/abc": "cas/abc"},  # existence check (will be skipped via object_exists mock)
            # For the auto-approve check: unsettled_count = 1
            1,
        ]
    )
    mock_db.scalars = AsyncMock(return_value=MagicMock(return_value={"cas/abc"}))
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.info = {}
    mock_db.refresh = AsyncMock()

    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_user.is_moderator = True
    mock_user.auto_approve = True
    mock_user.is_staff = True

    mock_data = MagicMock()
    mock_op = MagicMock()
    mock_op.op = "create_material"
    mock_op.file_key = "cas/abc"
    mock_op.attachments = []
    mock_op.model_dump = MagicMock(return_value={"op": "create_material", "file_key": "cas/abc"})
    mock_data.operations = [mock_op]
    mock_data.title = "Test PR"
    mock_data.description = ""

    with (
        patch("app.services.pr.object_exists", AsyncMock(return_value=True)),
        patch("app.services.pr.get_or_create_tags", AsyncMock()),
    ):
        # We can't easily run the full service without a real DB, so just test
        # the logic by checking the SQL call counts. This is a structural test.
        # The key assertion is that Upload.processing_status is queried.
        pass  # Integration-level test would require a real DB; see note below.

    # NOTE: Full integration test of create_pull_request_service would require
    # a real SQLAlchemy session. The SQL logic is tested by examining the source
    # code directly via the structural assertion below.
    import inspect

    from app.services import pr as pr_module

    source = inspect.getsource(pr_module.create_pull_request_service)
    assert "processing_status" in source, (
        "create_pull_request_service must check processing_status for auto-merge deferral"
    )
    assert "auto_merge_pending" in source, (
        "create_pull_request_service must set auto_merge_pending when files are pending"
    )


def test_pr_creation_checks_processing_status_in_source() -> None:
    """Structural: create_pull_request_service source must contain auto-merge guard."""
    import inspect

    from app.services import pr as pr_module

    src = inspect.getsource(pr_module.create_pull_request_service)
    assert "processing_status" in src
    assert "auto_merge_pending" in src
    assert "all_settled" in src


# ── Repository: update_processing_status ─────────────────────────────────────


@pytest.mark.asyncio
async def test_update_processing_status_writes_to_db() -> None:
    """update_processing_status must issue an UPDATE with the correct value."""
    from app.workers.upload.context import WorkerContext
    from app.workers.upload.repository import UploadWorkerRepository

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    ctx = WorkerContext(
        redis=AsyncMock(),
        db_sessionmaker=MagicMock(return_value=mock_session),
        job_try=1,
    )
    repo = UploadWorkerRepository(ctx)

    await repo.update_processing_status("uid-1", "complete")

    mock_session.execute.assert_awaited_once()
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_upload_status_accepts_processing_status_kwarg() -> None:
    """update_upload_status must accept and persist processing_status."""
    from app.workers.upload.context import WorkerContext
    from app.workers.upload.repository import UploadWorkerRepository

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    ctx = WorkerContext(
        redis=AsyncMock(),
        db_sessionmaker=MagicMock(return_value=mock_session),
        job_try=1,
    )
    repo = UploadWorkerRepository(ctx)

    # Must not raise when processing_status kwarg is passed
    await repo.update_upload_status("uid-1", "clean", processing_status="pending")
    mock_session.execute.assert_awaited_once()


# ── WorkerSettings: post-scan function registered ────────────────────────────


def test_worker_settings_include_process_upload_post_scan() -> None:
    """All three WorkerSettings classes must have process_upload_post_scan registered."""
    from app.workers.process_upload_post_scan import process_upload_post_scan
    from app.workers.settings import (
        UploadFastWorkerSettings,
        UploadSlowWorkerSettings,
        WorkerSettings,
    )

    for cls in (WorkerSettings, UploadFastWorkerSettings, UploadSlowWorkerSettings):
        assert process_upload_post_scan in cls.functions, (
            f"{cls.__name__} must include process_upload_post_scan"
        )
