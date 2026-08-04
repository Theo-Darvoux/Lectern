from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.upload_errors import UploadErrorCode
from app.core.database.redis import RedisSemaphoreTimeoutError
from app.routers.tus import tus_patch
from tests.test_tus import _create_user


@pytest.mark.asyncio
async def test_tus_checksum_missing_header(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    """Test Phase 1.4: Checksum is optional, upload should succeed without it."""
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = "00000000-0000-0000-0000-000000000000"
    content = b"content without checksum header"

    state = {
        "user_id": str(user.id),
        "offset": "0",
        "length": str(len(content)),
        "parts": "[]",
        "quarantine_key": "q/k",
        "s3_upload_id": "s3-id",
        "upload_id": "up-id",
        "filename": "f.txt",
        "mime_type": "text/plain",
    }
    await fake_redis_setup.hset(f"tus:state:{tus_id}", mapping=state)

    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        "Content-Type": "application/offset+octet-stream",
        "Upload-Offset": "0",
        "Content-Length": str(len(content)),
    }

    async def _stream():
        yield content

    mock_request.stream = _stream

    with (
        patch("app.routers.tus.upload_part", new_callable=AsyncMock) as m_upload,
        patch("app.routers.tus.complete_multipart_verified", new_callable=AsyncMock),
        patch("app.routers.tus._enqueue_processing", new_callable=AsyncMock),
    ):
        m_upload.return_value = "etag-123"
        import uuid

        response = await tus_patch(
            uuid.UUID(tus_id), mock_request, user, fake_redis_setup, db_session
        )
        assert response.status_code == 204
        assert response.headers["Upload-Offset"] == str(len(content))


@pytest.mark.asyncio
async def test_tus_concurrency_limit_hit(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    """Per-user semaphore saturation returns the TUS concurrency error."""
    user = await _create_user(db_session)
    await db_session.commit()
    tus_id = "00000000-0000-0000-0000-000000000000"

    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        "Content-Type": "application/offset+octet-stream",
        "Upload-Offset": "0",
        "Content-Length": "0",
    }

    with (
        patch(
            "app.routers.tus._load_state",
            new_callable=AsyncMock,
            return_value={
                "user_id": str(user.id),
                "upload_id": "upload-123",
                "offset": "0",
            },
        ),
        patch(
            "app.routers.tus.redis_semaphore",
            side_effect=RedisSemaphoreTimeoutError("full"),
        ),
    ):
        import uuid

        response = await tus_patch(
            uuid.UUID(tus_id), mock_request, user, fake_redis_setup, db_session
        )

    assert response.status_code == 429
    assert response.headers["X-Lectern-Error"] == UploadErrorCode.TUS_CONCURRENCY_LIMIT


@pytest.mark.asyncio
async def test_reconcile_skips_malicious_or_clean(fake_redis_setup):
    """Test Phase 2.2: Reconciliation skips files that are already finalized in DB."""
    from datetime import UTC, datetime, timedelta

    from app.workers.reconcile_multipart import reconcile_multipart_uploads

    mock_redis = AsyncMock()
    mock_redis.scan.return_value = (0, [])

    # Mock DB: one upload is 'clean', should be skipped even if old
    mock_session = AsyncMock()
    mock_session.scalars.return_value = ["up-clean"]
    mock_session_factory = MagicMock(return_value=mock_session)

    initiated = datetime.now(UTC) - timedelta(hours=3)
    # Key format: quarantine/{user_id}/{upload_id}/{filename}
    clean_part = {
        "UploadId": "s3-clean",
        "Key": "quarantine/u1/up-clean/f.txt",
        "Initiated": initiated,
    }

    async def mock_list_multipart(prefix):
        yield clean_part

    ctx = {"redis": mock_redis, "db_sessionmaker": mock_session_factory}

    with (
        patch(
            "app.workers.reconcile_multipart.list_multipart_uploads",
            side_effect=mock_list_multipart,
        ),
        patch(
            "app.workers.reconcile_multipart.abort_multipart_upload", new_callable=AsyncMock
        ) as m_abort,
    ):
        await reconcile_multipart_uploads(ctx)

        m_abort.assert_called_once()
