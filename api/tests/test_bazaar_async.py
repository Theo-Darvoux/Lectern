"""Tests for the fire-and-forget MalwareBazaar pattern.

Covers:
  - Pipeline skips Bazaar in scan_file_path when bazaar_async_enabled=True
  - Pipeline enqueues check_bazaar job after successful promotion
  - check_bazaar worker: clean path writes tombstone
  - check_bazaar worker: flagged path calls retroactive_quarantine
  - check_bazaar worker: tombstone idempotency
  - check_bazaar worker: timeout handling (fail-closed / fail-open)
  - retroactive_quarantine: marks DB malicious
  - retroactive_quarantine: idempotent durable CAS release
  - retroactive_quarantine: queues upload-owned CAS releases
  - retroactive_quarantine: skips releases for uploads without a CAS reference
  - retroactive_quarantine: soft-deletes MaterialVersion rows when enabled
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.models.material import Material, MaterialVersion
from app.models.outbox import OutboxJob
from app.models.upload import Upload
from app.workers.upload.context import WorkerContext

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ctx(redis: Any, session_factory: Any = None) -> WorkerContext:
    scanner_mock = MagicMock()
    scanner_mock.check_malwarebazaar = AsyncMock(return_value=None)
    scanner_mock.close = AsyncMock()
    return WorkerContext(
        redis=redis,
        db_sessionmaker=session_factory,
        job_try=1,
        scanner=scanner_mock,
    )


@pytest.fixture(autouse=True)
def _bypass_retroactive_lifecycle_lock() -> AsyncIterator[None]:
    @asynccontextmanager
    async def no_op_lock(*_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
        yield

    with patch("app.workers.retroactive_quarantine.redis_lock", new=no_op_lock):
        yield


def _make_arq_ctx(redis: Any, session_factory: Any = None) -> dict:
    """Build a minimal ARQ-style context dict."""
    scanner_mock = MagicMock()
    scanner_mock.check_malwarebazaar = AsyncMock(return_value=None)
    scanner_mock.close = AsyncMock()
    return {
        "redis": redis,
        "db_sessionmaker": session_factory,
        "job_try": 1,
        "scanner": scanner_mock,
    }


# ── Scanner path: YARA-only when bazaar_async_enabled=True ───────────────────


@pytest.mark.asyncio
async def test_scan_file_path_skips_bazaar_when_async_enabled(
    tmp_path,
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """When bazaar_async_enabled=True, scan_file_path must not call check_malwarebazaar."""
    from app.core.security.scanner import MalwareScanner

    scanner = MalwareScanner()
    # Patch rules so YARA scan returns None (no matches)
    rules_mock = MagicMock()
    rules_mock.match.return_value = []
    scanner.rules = rules_mock
    scanner.client = AsyncMock()

    test_file = tmp_path / "file.pdf"
    test_file.write_bytes(b"%PDF-1.4 test content")

    with patch("app.core.security.scanner.settings") as mock_settings:
        mock_settings.bazaar_async_enabled = True
        mock_settings.yara_scan_timeout = 10
        bazaar_spy = AsyncMock(return_value=None)
        scanner.check_malwarebazaar = bazaar_spy

        await scanner.scan_file_path(test_file, "file.pdf")

    bazaar_spy.assert_not_called()


# ── Pipeline: enqueues check_bazaar after CLEAN ───────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_enqueues_check_bazaar_on_clean(mock_redis: AsyncMock) -> None:
    """_fast_finalize_and_enqueue_post_scan must enqueue check_bazaar when bazaar_async_enabled=True."""
    from pathlib import Path

    from app.workers.upload.pipeline import UploadPipeline

    arq_pool_mock = AsyncMock()
    arq_pool_mock.enqueue_job = AsyncMock()

    ctx = WorkerContext(
        redis=mock_redis,
        db_sessionmaker=None,
        job_try=1,
        scanner=None,
    )
    pipeline = UploadPipeline(
        ctx,
        user_id="user-123",
        upload_id="upload-abc",
        quarantine_key="quarantine/user-123/upload-abc/file.pdf",
        original_filename="file.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )
    import time

    pipeline.original_sha256 = "deadbeef" * 8  # 64-char sha256
    pipeline.initial_size = 1024
    pipeline.mime_category = "document"
    pipeline.pipeline_start = time.monotonic()
    pipeline.cas_key = "upload:cas:abc"
    pipeline.pf = MagicMock()
    pipeline.tmp_path = Path("/tmp/t")

    final_res_mock = MagicMock()
    final_res_mock.final_key = "cas/abc123"
    final_res_mock.safe_name = "file.pdf"
    final_res_mock.final_size = 900
    final_res_mock.content_sha256 = "content_sha"
    final_res_mock.db_cas_key = "upload:cas:abc"
    final_res_mock.new_cas_ref = 1

    with (
        patch("app.workers.upload.pipeline.settings") as mock_settings,
        patch(
            "app.workers.upload.pipeline.run_finalize_storage",
            AsyncMock(return_value=final_res_mock),
        ),
        patch("app.core.database.redis.arq_pool", arq_pool_mock),
    ):
        mock_settings.bazaar_async_enabled = True
        mock_settings.malwarebazaar_fail_closed = False
        mock_settings.upload_pipeline_max_seconds = 600
        pipeline.cache = MagicMock()
        pipeline.cache.emit_event = AsyncMock()
        pipeline.repo = MagicMock()
        pipeline.repo.publish_clean_upload = AsyncMock()
        pipeline.repo.update_upload_status = AsyncMock()
        pipeline.repo.update_processing_status = AsyncMock()

        await pipeline._fast_finalize_and_enqueue_post_scan()

    enqueued_job_names = [call.args[0] for call in arq_pool_mock.enqueue_job.call_args_list]
    assert "check_bazaar" in enqueued_job_names, (
        f"check_bazaar must be enqueued when bazaar_async_enabled=True; got {enqueued_job_names}"
    )
    bazaar_call = next(
        c for c in arq_pool_mock.enqueue_job.call_args_list if c.args[0] == "check_bazaar"
    )
    assert bazaar_call.kwargs["sha256"] == "deadbeef" * 8
    assert bazaar_call.kwargs["cas_s3_key"] == "cas/abc123"
    assert bazaar_call.kwargs["upload_id"] == "upload-abc"


@pytest.mark.asyncio
async def test_pipeline_no_enqueue_when_bazaar_async_disabled(mock_redis: AsyncMock) -> None:
    """When bazaar_async_enabled=False, no check_bazaar job must be enqueued."""
    from pathlib import Path

    from app.workers.upload.pipeline import UploadPipeline

    arq_pool_mock = AsyncMock()
    arq_pool_mock.enqueue_job = AsyncMock()

    scanner = MagicMock()
    scanner.check_malwarebazaar = AsyncMock(return_value=None)
    ctx = WorkerContext(redis=mock_redis, db_sessionmaker=None, job_try=1, scanner=scanner)
    pipeline = UploadPipeline(
        ctx,
        user_id="user-123",
        upload_id="upload-abc",
        quarantine_key="quarantine/user-123/upload-abc/file.pdf",
        original_filename="file.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )
    import time

    pipeline.original_sha256 = "deadbeef" * 8
    pipeline.initial_size = 1024
    pipeline.mime_category = "document"
    pipeline.pipeline_start = time.monotonic()
    pipeline.cas_key = "upload:cas:abc"
    pipeline.pf = MagicMock()
    pipeline.tmp_path = Path("/tmp/t")

    final_res_mock = MagicMock()
    final_res_mock.final_key = "cas/abc123"
    final_res_mock.safe_name = "file.pdf"
    final_res_mock.final_size = 900
    final_res_mock.content_sha256 = "content_sha"
    final_res_mock.db_cas_key = "upload:cas:abc"
    final_res_mock.new_cas_ref = 1

    with (
        patch("app.workers.upload.pipeline.settings") as mock_settings,
        patch(
            "app.workers.upload.pipeline.run_finalize_storage",
            AsyncMock(return_value=final_res_mock),
        ),
        patch("app.core.database.redis.arq_pool", arq_pool_mock),
    ):
        mock_settings.bazaar_async_enabled = False
        mock_settings.malwarebazaar_fail_closed = False
        mock_settings.upload_pipeline_max_seconds = 600
        pipeline.cache = MagicMock()
        pipeline.cache.emit_event = AsyncMock()
        pipeline.repo = MagicMock()
        pipeline.repo.publish_clean_upload = AsyncMock()
        pipeline.repo.update_upload_status = AsyncMock()
        pipeline.repo.update_processing_status = AsyncMock()

        await pipeline._fast_finalize_and_enqueue_post_scan()

    enqueued_job_names = [call.args[0] for call in arq_pool_mock.enqueue_job.call_args_list]
    assert "check_bazaar" not in enqueued_job_names, (
        f"check_bazaar must NOT be enqueued when bazaar_async_enabled=False; got {enqueued_job_names}"
    )
    scanner.check_malwarebazaar.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_closed_bazaar_error_prevents_publication(mock_redis: AsyncMock) -> None:
    """Fail-closed mode checks Bazaar before CAS promotion or CLEAN publication."""
    from pathlib import Path

    from app.workers.upload.pipeline import UploadPipeline

    scanner = MagicMock()
    scanner.check_malwarebazaar = AsyncMock(side_effect=RuntimeError("Bazaar unavailable"))
    ctx = WorkerContext(redis=mock_redis, db_sessionmaker=None, job_try=1, scanner=scanner)
    pipeline = UploadPipeline(
        ctx,
        user_id="user-123",
        upload_id="upload-abc",
        quarantine_key="quarantine/user-123/upload-abc/file.pdf",
        original_filename="file.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )
    pipeline.original_sha256 = "deadbeef" * 8
    pipeline.initial_size = 1024
    pipeline.cas_key = "upload:cas:abc"
    pipeline.pf = MagicMock()
    pipeline.tmp_path = Path("/tmp/t")
    pipeline.cache = MagicMock()
    pipeline.cache.emit_event = AsyncMock()
    pipeline.repo = MagicMock()
    pipeline.repo.update_upload_status = AsyncMock()
    mock_redis.get.return_value = None
    finalize = AsyncMock()

    with (
        patch("app.workers.upload.pipeline.settings") as mock_settings,
        patch("app.workers.upload.pipeline.run_finalize_storage", finalize),
        pytest.raises(RuntimeError, match="Bazaar unavailable"),
    ):
        mock_settings.bazaar_async_enabled = True
        mock_settings.malwarebazaar_fail_closed = True
        mock_settings.upload_pipeline_max_seconds = 600
        await pipeline._fast_finalize_and_enqueue_post_scan()

    finalize.assert_not_awaited()
    emitted = [call.args[3] for call in pipeline.cache.emit_event.await_args_list]
    assert all('"status": "clean"' not in payload for payload in emitted)


# ── check_bazaar worker ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_bazaar_clean_writes_tombstone(
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """check_bazaar must write bazaar:clean:{sha256} tombstone when Bazaar returns None."""
    from app.workers.check_bazaar import check_bazaar

    sha256 = "aabbccdd" * 8
    ctx = _make_arq_ctx(mock_redis)
    ctx["scanner"].check_malwarebazaar = AsyncMock(return_value=None)

    await check_bazaar(
        ctx,
        upload_id="upload-abc",
        sha256=sha256,
        cas_s3_key="cas/abc123",
        user_id="user-123",
    )

    tombstone = await mock_redis.get(f"bazaar:clean:{sha256}")
    assert tombstone is not None


@pytest.mark.asyncio
async def test_check_bazaar_idempotent_via_tombstone(
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """check_bazaar must skip Bazaar call if tombstone already exists."""
    from app.workers.check_bazaar import _BAZAAR_CLEAN_PREFIX, check_bazaar

    sha256 = "aabbccdd" * 8
    # Pre-seed the tombstone
    await mock_redis.set(f"{_BAZAAR_CLEAN_PREFIX}{sha256}", "1", ex=3600)

    ctx = _make_arq_ctx(mock_redis)
    scanner_spy = AsyncMock(return_value=None)
    ctx["scanner"].check_malwarebazaar = scanner_spy

    await check_bazaar(
        ctx,
        upload_id="upload-abc",
        sha256=sha256,
        cas_s3_key="cas/abc123",
        user_id="user-123",
    )

    scanner_spy.assert_not_called()


@pytest.mark.asyncio
async def test_check_bazaar_flagged_calls_retroactive_quarantine(
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """check_bazaar must delegate to retroactive_quarantine when Bazaar returns a threat."""
    from app.workers.check_bazaar import check_bazaar

    sha256 = "aabbccdd" * 8
    ctx = _make_arq_ctx(mock_redis)
    ctx["scanner"].check_malwarebazaar = AsyncMock(return_value="Mirai.Botnet")

    rq_mock = AsyncMock()
    with patch("app.workers.check_bazaar.retroactive_quarantine", rq_mock):
        await check_bazaar(
            ctx,
            upload_id="upload-abc",
            sha256=sha256,
            cas_s3_key="cas/abc123",
            user_id="user-123",
        )

    rq_mock.assert_awaited_once()
    call_kwargs = rq_mock.call_args.kwargs
    assert call_kwargs["threat"] == "Mirai.Botnet"
    assert call_kwargs["sha256"] == sha256


@pytest.mark.asyncio
async def test_check_bazaar_timeout_fail_closed(
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """check_bazaar must re-raise TimeoutException when malwarebazaar_fail_closed=True."""
    from app.workers.check_bazaar import check_bazaar

    sha256 = "aabbccdd" * 8
    ctx = _make_arq_ctx(mock_redis)
    ctx["scanner"].check_malwarebazaar = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    with (
        patch("app.workers.check_bazaar.settings") as mock_settings,
        pytest.raises(httpx.TimeoutException),
    ):
        mock_settings.malwarebazaar_fail_closed = True
        await check_bazaar(
            ctx,
            upload_id="upload-abc",
            sha256=sha256,
            cas_s3_key="cas/abc123",
            user_id="user-123",
        )


@pytest.mark.asyncio
async def test_check_bazaar_timeout_fail_open(
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """check_bazaar must write skip tombstone and NOT raise when fail_closed=False."""
    from app.workers.check_bazaar import _BAZAAR_SKIPPED_PREFIX, check_bazaar

    sha256 = "aabbccdd" * 8
    ctx = _make_arq_ctx(mock_redis)
    ctx["scanner"].check_malwarebazaar = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    with patch("app.workers.check_bazaar.settings") as mock_settings:
        mock_settings.malwarebazaar_fail_closed = False
        # Should NOT raise
        await check_bazaar(
            ctx,
            upload_id="upload-abc",
            sha256=sha256,
            cas_s3_key="cas/abc123",
            user_id="user-123",
        )

    tombstone = await mock_redis.get(f"{_BAZAAR_SKIPPED_PREFIX}{sha256}")
    assert tombstone is not None


# ── retroactive_quarantine ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retroactive_quarantine_marks_upload_malicious(
    db_session,
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """retroactive_quarantine must set Upload.status = 'malicious'."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.workers.retroactive_quarantine import retroactive_quarantine

    # Create an upload row in the test DB
    upload = Upload(
        upload_id="upload-rq-001",
        user_id=uuid.uuid4(),
        quarantine_key="quarantine/u/id/file.pdf",
        status="clean",
        filename="file.pdf",
        content_sha256="aabbccdd" * 8,
        cas_ref_count=1,
    )
    db_session.add(upload)
    await db_session.commit()

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    sha256 = "aabbccdd" * 8
    ctx = _make_ctx(mock_redis, session_factory)

    with (
        patch(
            "app.workers.retroactive_quarantine.dispatch_post_commit_actions",
            new_callable=AsyncMock,
        ) as dispatch_mock,
        patch("app.workers.retroactive_quarantine.settings") as mock_settings,
    ):
        mock_settings.bazaar_retroactive_check_materials = False

        await retroactive_quarantine(
            ctx,
            upload_id="upload-rq-001",
            sha256=sha256,
            cas_s3_key="cas/abc123",
            user_id=str(upload.user_id),
            threat="Mirai.Botnet",
        )

    # Re-fetch from DB
    async with session_factory() as session:
        row = await session.scalar(select(Upload).where(Upload.upload_id == "upload-rq-001"))
    assert row is not None
    assert row.status == "malicious"
    assert "Mirai.Botnet" in (row.error_detail or "")
    assert row.cas_ref_count == 0
    async with session_factory() as session:
        jobs = list((await session.scalars(select(OutboxJob))).all())
    assert len(jobs) == 1
    assert jobs[0].job_name == "release_cas_references"
    assert jobs[0].args == [
        [
            {
                "sha256": sha256,
                "operation_id": "quarantine:upload:upload-rq-001:release",
            }
        ]
    ]
    dispatch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_retroactive_quarantine_idempotent(
    db_session,
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """A retry repairs an interrupted malicious transition without double release."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.workers.retroactive_quarantine import retroactive_quarantine

    upload = Upload(
        upload_id="upload-rq-idem",
        user_id=uuid.uuid4(),
        quarantine_key="quarantine/u/id/file.pdf",
        status="malicious",  # Already terminal
        filename="file.pdf",
        content_sha256="aabbccdd" * 8,
        cas_ref_count=1,
    )
    db_session.add(upload)
    await db_session.commit()

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    sha256 = "aabbccdd" * 8
    ctx = _make_ctx(mock_redis, session_factory)

    with (
        patch(
            "app.workers.retroactive_quarantine.dispatch_post_commit_actions",
            new_callable=AsyncMock,
        ) as dispatch_mock,
        patch("app.workers.retroactive_quarantine.settings") as mock_settings,
    ):
        mock_settings.bazaar_retroactive_check_materials = False

        # Call twice — second must be a no-op
        await retroactive_quarantine(
            ctx,
            upload_id="upload-rq-idem",
            sha256=sha256,
            cas_s3_key="cas/abc123",
            user_id=str(upload.user_id),
            threat="Mirai",
        )
        await retroactive_quarantine(
            ctx,
            upload_id="upload-rq-idem",
            sha256=sha256,
            cas_s3_key="cas/abc123",
            user_id=str(upload.user_id),
            threat="Mirai",
        )

    async with session_factory() as session:
        jobs = list((await session.scalars(select(OutboxJob))).all())
        row = await session.scalar(select(Upload).where(Upload.upload_id == "upload-rq-idem"))
    assert len(jobs) == 1
    assert jobs[0].args[0][0] == {
        "sha256": sha256,
        "operation_id": "quarantine:upload:upload-rq-idem:release",
    }
    assert row is not None
    assert row.cas_ref_count == 0
    dispatch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_retroactive_quarantine_queues_upload_cas_release(
    db_session,
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """CAS cleanup is represented by a durable, uniquely identified release job."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.workers.retroactive_quarantine import retroactive_quarantine

    upload = Upload(
        upload_id="upload-rq-shared",
        user_id=uuid.uuid4(),
        quarantine_key="quarantine/u/id/file.pdf",
        status="clean",
        filename="file.pdf",
        content_sha256="bbccddaa" * 8,
        cas_ref_count=1,
    )
    db_session.add(upload)
    await db_session.commit()

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    sha256 = "aabbccdd" * 8

    ctx = _make_ctx(mock_redis, session_factory)

    with (
        patch(
            "app.workers.retroactive_quarantine.dispatch_post_commit_actions",
            new_callable=AsyncMock,
        ),
        patch("app.workers.retroactive_quarantine.settings") as mock_settings,
    ):
        mock_settings.bazaar_retroactive_check_materials = False

        await retroactive_quarantine(
            ctx,
            upload_id="upload-rq-shared",
            sha256=sha256,
            cas_s3_key="cas/abc123",
            user_id=str(upload.user_id),
            threat="Mirai",
        )

    async with session_factory() as session:
        jobs = list((await session.scalars(select(OutboxJob))).all())
    assert len(jobs) == 1
    assert jobs[0].args[0][0] == {
        "sha256": "bbccddaa" * 8,
        "operation_id": "quarantine:upload:upload-rq-shared:release",
    }


@pytest.mark.asyncio
async def test_retroactive_quarantine_skips_release_when_upload_has_no_cas_ref(
    db_session,
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """An upload that owns no CAS reference must not queue a release."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.workers.retroactive_quarantine import retroactive_quarantine

    upload = Upload(
        upload_id="upload-rq-delete",
        user_id=uuid.uuid4(),
        quarantine_key="quarantine/u/id/file.pdf",
        status="clean",
        filename="file.pdf",
        content_sha256="aabbccdd" * 8,
        cas_ref_count=0,
    )
    db_session.add(upload)
    await db_session.commit()

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    sha256 = "aabbccdd" * 8

    ctx = _make_ctx(mock_redis, session_factory)

    with (
        patch(
            "app.workers.retroactive_quarantine.dispatch_post_commit_actions",
            new_callable=AsyncMock,
        ),
        patch("app.workers.retroactive_quarantine.settings") as mock_settings,
    ):
        mock_settings.bazaar_retroactive_check_materials = False

        await retroactive_quarantine(
            ctx,
            upload_id="upload-rq-delete",
            sha256=sha256,
            cas_s3_key="cas/abc123",
            user_id=str(upload.user_id),
            threat="Mirai",
        )

    async with session_factory() as session:
        jobs = list((await session.scalars(select(OutboxJob))).all())
        row = await session.scalar(select(Upload).where(Upload.upload_id == "upload-rq-delete"))
    assert jobs == []
    assert row is not None
    assert row.status == "malicious"
    assert row.cas_ref_count == 0


@pytest.mark.asyncio
async def test_retroactive_quarantine_soft_deletes_material_version(
    db_session,
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """retroactive_quarantine must soft-delete MaterialVersion rows when enabled."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.workers.retroactive_quarantine import retroactive_quarantine

    sha256 = "aabbccdd" * 8
    cas_s3_key = "cas/abc123"

    # Create a Material + MaterialVersion referencing the flagged sha256
    material = Material(
        title="Malware Doc",
        slug="malware-doc",
        type="document",
    )
    db_session.add(material)
    await db_session.flush()

    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        file_key=cas_s3_key,
        cas_sha256=sha256,
    )
    db_session.add(version)

    upload = Upload(
        upload_id="upload-rq-mat",
        user_id=uuid.uuid4(),
        quarantine_key="quarantine/u/id/file.pdf",
        status="clean",
        filename="file.pdf",
    )
    db_session.add(upload)
    await db_session.commit()

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    ctx = _make_ctx(mock_redis, session_factory)

    with (
        patch(
            "app.workers.retroactive_quarantine.dispatch_post_commit_actions",
            new_callable=AsyncMock,
        ),
        patch("app.workers.retroactive_quarantine.settings") as mock_settings,
    ):
        mock_settings.bazaar_retroactive_check_materials = True

        await retroactive_quarantine(
            ctx,
            upload_id="upload-rq-mat",
            sha256=sha256,
            cas_s3_key=cas_s3_key,
            user_id=str(upload.user_id),
            threat="Mirai",
        )

    # Verify the MaterialVersion was soft-deleted
    # We must refresh or re-fetch to see the changes made by the other session
    version_id = version.id
    db_session.expire_all()
    v = await db_session.get(MaterialVersion, version_id)
    assert v is not None
    assert v.deleted_at is not None, "MaterialVersion should have been soft-deleted"
    async with session_factory() as session:
        jobs = list((await session.scalars(select(OutboxJob))).all())
    assert len(jobs) == 1
    assert jobs[0].job_name == "release_cas_references"
    assert jobs[0].args == [
        [
            {
                "sha256": sha256,
                "operation_id": f"quarantine:version:{version_id}:release",
            }
        ]
    ]
