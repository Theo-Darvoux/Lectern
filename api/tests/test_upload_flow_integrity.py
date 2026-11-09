"""Regression tests for upload retry, resume, quota, and admission invariants."""

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.upload import Upload
from app.routers.upload.helpers import (
    _MULTIPART_INTENT_TTL,
    _UPLOAD_INTENT_PREFIX,
    _processing_queue_name,
)
from app.schemas.material import UploadStatus
from app.workers.upload.context import WorkerContext
from app.workers.upload.exceptions import UploadError
from app.workers.upload.pipeline import UploadPipeline
from tests.test_tus import _auth_headers, _create_user

_PART = 8 * 1024 * 1024


def _pipeline(*, job_try: int = 1, scanner: Any = None) -> UploadPipeline:
    pipeline = UploadPipeline(
        WorkerContext(redis=AsyncMock(), db_sessionmaker=None, job_try=job_try, scanner=scanner),
        user_id="user-1",
        upload_id="upload-1",
        quarantine_key="quarantine/user-1/upload-1/file.pdf",
        original_filename="file.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )
    pipeline.cache = MagicMock()
    pipeline.cache.emit_event = AsyncMock()
    pipeline.cache.is_cancelled = AsyncMock(return_value=False)
    pipeline.repo = MagicMock()
    pipeline.repo.get_pipeline_stage = AsyncMock(return_value=0)
    pipeline.repo.update_upload_status = AsyncMock(return_value=True)
    pipeline.repo.checkpoint_pipeline_stage = AsyncMock()
    pipeline.repo.insert_dead_letter = AsyncMock()
    return pipeline


@pytest.mark.parametrize(
    ("mime_type", "size", "queue"),
    [
        ("application/pdf", 200 * 1024, "upload-fast"),
        ("application/pdf", 50 * 1024 * 1024, "upload-slow"),
        ("video/mp4", 1024, "upload-slow"),
        ("image/png", 40 * 1024 * 1024, "upload-fast"),
    ],
)
def test_queue_routing_keeps_short_documents_out_of_the_slow_queue(
    mime_type: str, size: int, queue: str
) -> None:
    assert _processing_queue_name(mime_type, size) == queue


@pytest.mark.asyncio
async def test_rerun_after_publication_does_not_reprocess() -> None:
    pipeline = _pipeline()
    pipeline.repo.get_pipeline_stage = AsyncMock(return_value=5)

    with patch(
        "app.workers.upload.pipeline.run_download_and_validate", new_callable=AsyncMock
    ) as download:
        await pipeline.run()

    download.assert_not_awaited()
    pipeline.repo.update_upload_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_rerun_before_publication_sanitizes_again(tmp_path: Path) -> None:
    pipeline = _pipeline()
    pipeline.repo.get_pipeline_stage = AsyncMock(return_value=2)
    pipeline.pf = MagicMock()
    pipeline.tmp_path = tmp_path / "input"
    pipeline._fast_finalize_and_enqueue_post_scan = AsyncMock()  # type: ignore[method-assign]

    with (
        patch("app.workers.upload.pipeline.run_scan_and_strip", new_callable=AsyncMock) as scan,
        patch("app.workers.upload.pipeline.run_post_strip_pdf_check", AsyncMock()),
    ):
        await pipeline._run_stages()

    scan.assert_awaited_once()


@pytest.mark.asyncio
async def test_last_attempt_reports_failure_and_dead_letters() -> None:
    pipeline = _pipeline(job_try=3)

    with (
        patch(
            "app.workers.upload.pipeline.run_download_and_validate",
            AsyncMock(side_effect=RuntimeError("storage failure")),
        ),
        patch("app.workers.upload.pipeline.release_storage_reservation", AsyncMock()),
        patch("app.workers.upload.pipeline.delete_object", AsyncMock()),
    ):
        await pipeline.run()

    pipeline.repo.insert_dead_letter.assert_awaited_once()
    payloads = [json.loads(call.args[3]) for call in pipeline.cache.emit_event.await_args_list]
    assert payloads[-1]["status"] == UploadStatus.FAILED
    assert payloads[-1]["error_code"] == "ERR_PROCESSING_FAILED"


@pytest.mark.asyncio
async def test_terminal_failure_releases_pending_quota() -> None:
    pipeline = _pipeline()

    with (
        patch(
            "app.workers.upload.pipeline.run_download_and_validate",
            AsyncMock(side_effect=UploadError(UploadStatus.FAILED, "rejected")),
        ),
        patch("app.workers.upload.pipeline.release_storage_reservation", AsyncMock()),
    ):
        await pipeline.run()

    pipeline.redis.zrem.assert_awaited_with(
        "quota:uploads:user-1",
        "quarantine/user-1/upload-1/file.pdf",
    )


@pytest.mark.asyncio
async def test_damaged_file_is_failed_not_reported_as_malware(tmp_path: Path) -> None:
    from app.core.security.file_security.errors import UnreadableFileError
    from app.workers.upload.stages.scan_strip import run_scan_and_strip

    source = tmp_path / "damaged.pdf"
    source.write_bytes(b"%PDF-1.4")
    pf = MagicMock()

    with (
        patch(
            "app.workers.upload.stages.scan_strip.parallel_tasks",
            AsyncMock(return_value=[None, UnreadableFileError("Failed to sanitize")]),
        ),
        pytest.raises(UploadError) as raised,
    ):
        scan = _pipeline(scanner=MagicMock(scan_file_path=AsyncMock()))
        await run_scan_and_strip(
            scan.ctx,
            pf,
            source,
            "damaged.pdf",
            "a" * 64,
            "application/pdf",
            "pdf",
            "id",
            MagicMock(),
        )

    assert raised.value.status == UploadStatus.FAILED
    assert raised.value.error_code == "ERR_FILE_UNREADABLE"


@pytest.mark.asyncio
async def test_bazaar_lookup_is_skipped_without_an_api_key() -> None:
    scanner = MagicMock(check_malwarebazaar=AsyncMock(return_value=None))
    pipeline = _pipeline(scanner=scanner)

    with (
        patch("app.workers.upload.pipeline.settings.malware_scan_enabled", True),
        patch("app.workers.upload.pipeline.settings.malwarebazaar_api_key", None),
    ):
        await pipeline._check_bazaar_before_finalize()

    scanner.check_malwarebazaar.assert_not_awaited()
    pipeline.redis.set.assert_not_awaited()


async def _store_intent(fake_redis: Any, user_id: str, upload_id: str, **extra: Any) -> None:
    await fake_redis.set(
        f"{_UPLOAD_INTENT_PREFIX}{upload_id}",
        json.dumps(
            {
                "user_id": user_id,
                "upload_id": upload_id,
                "quarantine_key": f"quarantine/{user_id}/{upload_id}/large.pdf",
                "s3_multipart_id": "s3-upload",
                "filename": "large.pdf",
                "mime_type": "application/pdf",
                "size": 2 * _PART + 1024,
                "part_size": _PART,
                "num_parts": 3,
                **extra,
            }
        ),
        ex=_MULTIPART_INTENT_TTL,
    )


@pytest.mark.asyncio
async def test_resume_returns_stored_etags_and_signs_only_missing_parts(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    await _store_intent(fake_redis_setup, str(user.id), upload_id)

    with (
        patch(
            "app.routers.upload.presigned.list_uploaded_parts",
            AsyncMock(
                return_value=[
                    {"PartNumber": 1, "ETag": '"one"', "Size": _PART},
                    # A part with the wrong size must be sent again.
                    {"PartNumber": 2, "ETag": '"short"', "Size": 1},
                ]
            ),
        ),
        patch(
            "app.routers.upload.presigned.generate_presigned_upload_part",
            AsyncMock(return_value="http://signed"),
        ) as sign,
    ):
        response = await client.post(
            f"/api/upload/presigned-multipart/{upload_id}/resume",
            headers=_auth_headers(user),
        )

    assert response.status_code == 200
    parts = response.json()["parts"]
    assert [(part["etag"], part["url"]) for part in parts] == [
        ('"one"', None),
        (None, "http://signed"),
        (None, "http://signed"),
    ]
    assert sign.await_count == 2


@pytest.mark.asyncio
async def test_resume_replays_checkpointed_manifest(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    manifest = [{"PartNumber": n, "ETag": f'"{n}"'} for n in (1, 2, 3)]
    await _store_intent(fake_redis_setup, str(user.id), upload_id, part_manifest=manifest)

    with patch(
        "app.routers.upload.presigned.list_uploaded_parts", new_callable=AsyncMock
    ) as list_parts:
        response = await client.post(
            f"/api/upload/presigned-multipart/{upload_id}/resume",
            headers=_auth_headers(user),
        )

    assert response.status_code == 200
    assert [part["etag"] for part in response.json()["parts"]] == ['"1"', '"2"', '"3"']
    list_parts.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_rejects_failed_upload(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    upload_id = str(uuid.uuid4())
    db_session.add(
        Upload(
            upload_id=upload_id,
            user_id=user.id,
            quarantine_key=f"quarantine/{user.id}/{upload_id}/large.pdf",
            filename="large.pdf",
            mime_type="application/pdf",
            size_bytes=2 * _PART + 1024,
            status="failed",
        )
    )
    await db_session.commit()
    await _store_intent(fake_redis_setup, str(user.id), upload_id)

    response = await client.post(
        f"/api/upload/presigned-multipart/{upload_id}/resume",
        headers=_auth_headers(user),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_resume_rejects_another_users_intent(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    await _store_intent(fake_redis_setup, str(uuid.uuid4()), upload_id)

    response = await client.post(
        f"/api/upload/presigned-multipart/{upload_id}/resume",
        headers=_auth_headers(user),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_multipart_init_honors_operator_allowlist(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    with (
        patch("app.routers.upload.presigned.settings.enable_presigned_multipart", True),
        patch("app.core.common.upload_limits.settings.allowed_extensions", ".png"),
    ):
        response = await client.post(
            "/api/upload/presigned-multipart/init",
            headers=_auth_headers(user),
            json={
                "filename": "large.pdf",
                "size": 20 * 1024 * 1024,
                "mime_type": "application/pdf",
            },
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_TYPE_NOT_ALLOWED"
