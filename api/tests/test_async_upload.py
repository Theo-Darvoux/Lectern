import io
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.schemas.material import UploadStatus


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.commit()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_async_upload_flow(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
    mock_arq_pool: AsyncMock,
):
    """Test the full async upload flow: POST → 202 → status polling."""
    user = await _create_user(db_session)
    headers = _auth_headers(user)

    file_content = b"%PDF-1.4\ncontent"
    files = {"file": ("test.pdf", io.BytesIO(file_content), "application/pdf")}

    with (
        patch("app.routers.upload.direct.get_s3_client") as mock_s3_cm,
        patch("app.dependencies.auth.is_token_blacklisted", new_callable=AsyncMock) as mock_bl,
        patch("app.dependencies.auth.is_session_revoked", new_callable=AsyncMock) as mock_session,
    ):
        mock_s3 = AsyncMock()
        mock_s3_cm.return_value.__aenter__.return_value = mock_s3
        mock_bl.return_value = False
        mock_session.return_value = False

        # 1. POST Upload → 202 Accepted
        resp = await client.post("/api/upload", files=files, headers=headers)
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == UploadStatus.PENDING
        quarantine_key = data["file_key"]
        assert quarantine_key.startswith(f"quarantine/{user.id}/")

        # Verify processing job enqueued
        mock_arq_pool.enqueue_job.assert_called_once()
        assert mock_arq_pool.enqueue_job.call_args[0][0] == "process_upload"

        # 2. GET /status → pending (worker hasn't updated Redis yet)
        mock_redis.get.return_value = json.dumps(data)
        resp = await client.get(f"/api/upload/status/{quarantine_key}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == UploadStatus.PENDING

        # 3. GET /status → clean (simulate worker writing final status to Redis)
        final_data = {
            "file_key": quarantine_key,
            "status": UploadStatus.CLEAN,
            "result": {
                "file_key": f"uploads/{user.id}/uuid/test.pdf",
                "size": 100,
                "original_size": 100,
                "mime_type": "application/pdf",
            },
        }
        mock_redis.get.return_value = json.dumps(final_data)
        resp = await client.get(f"/api/upload/status/{quarantine_key}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == UploadStatus.CLEAN
        assert resp.json()["result"]["file_key"].startswith("uploads/")


@pytest.mark.asyncio
async def test_worker_process_upload_logic():
    """Test the internal logic of the process_upload worker function."""
    from app.workers.process_upload import process_upload

    mock_redis = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=0)  # Not cancelled
    mock_redis.eval = AsyncMock(return_value=1)  # CAS increment returns 1
    mock_redis.rpush = AsyncMock(return_value=1)  # rpush returns list length as int
    mock_redis.get = AsyncMock(return_value=None)  # No cached CAS entries
    ctx = {"redis": mock_redis}
    user_id = str(uuid.uuid4())
    upload_id = str(uuid.uuid4())
    q_key = f"quarantine/{user_id}/{upload_id}/hash/test.pdf"

    with (
        patch(
            "app.workers.upload.stages.download.download_file_with_hash", new_callable=AsyncMock
        ) as mock_dl,
        patch(
            "app.workers.upload.stages.download.get_object_info", new_callable=AsyncMock
        ) as mock_info,
        patch(
            "app.workers.upload.stages.download.inspect_upload", new_callable=AsyncMock
        ) as mock_inspect,
        patch(
            "app.workers.upload.stages.scan_strip.check_pdf_safety_isolated",
            new_callable=AsyncMock,
        ),
        patch("app.workers.upload.pipeline.MalwareScanner") as mock_scanner_cls,
        patch(
            "app.workers.upload.stages.scan_strip.sanitize_upload", new_callable=AsyncMock
        ) as mock_sanitize,
        patch(
            "app.workers.upload.stages.compress.compress_file_path", new_callable=AsyncMock
        ) as mock_comp,
        patch("app.workers.upload.stages.finalize.upload_file_multipart", new_callable=AsyncMock),
        patch("app.workers.upload.pipeline.delete_object", new_callable=AsyncMock),
        patch(
            "app.core.events.processing.ProcessingFile.sha256", new_callable=AsyncMock
        ) as mock_sha,
    ):
        mock_dl.return_value = "mocksha256"
        mock_info.return_value = {"size": 100, "content_type": "application/pdf"}
        from app.core.security.isolated_parser import UploadInspection

        mock_inspect.return_value = UploadInspection(
            actual_mime="application/pdf",
                uncompressed_size=None,
                parser_pid=1234,
                parser_uid=1000,
            )

        mock_sha.return_value = "fake-sha"

        # Mock scanner instance
        mock_scanner = MagicMock()
        mock_scanner.scan_file_path = AsyncMock()
        mock_scanner.check_malwarebazaar = AsyncMock(return_value=None)
        mock_scanner.close = AsyncMock()
        mock_scanner_cls.return_value = mock_scanner

        # Mock results
        import tempfile
        from pathlib import Path

        t1 = tempfile.NamedTemporaryFile(delete=False)
        t1.write(b"clean")
        t1.close()
        clean_path = Path(t1.name)

        t2 = tempfile.NamedTemporaryFile(delete=False)
        t2.write(b"comp")
        t2.close()
        comp_path = Path(t2.name)

        mock_sanitize.return_value = clean_path

        from app.core.security.file_security import CompressResultPath

        mock_comp.return_value = CompressResultPath(comp_path, 500, None, "application/pdf")

        try:
            await process_upload(ctx, user_id, upload_id, q_key, "test.pdf", "application/pdf")
        finally:
            clean_path.unlink(missing_ok=True)
            comp_path.unlink(missing_ok=True)

        # Check that it updated status to CLEAN at the end
        # Last call to redis.set should be CLEAN
        found_clean = False
        for call in ctx["redis"].set.call_args_list:
            raw_val = call[0][1]
            try:
                val = json.loads(raw_val)
                if isinstance(val, dict) and val.get("status") == UploadStatus.CLEAN:
                    found_clean = True
            except (json.JSONDecodeError, TypeError):
                continue
        if not found_clean:
            print(ctx["redis"].set.call_args_list)
        assert found_clean
