import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from app.routers.tus import tus_options, tus_patch


@pytest.mark.asyncio
async def test_tus_options_advertises_checksum():
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    response = await tus_options(mock_redis)
    assert response.headers["Tus-Extension"].find("checksum") != -1
    assert response.headers["Tus-Checksum-Algorithm"] == "sha256"


def _mock_tus_request(content: bytes, checksum_header: str | None = None) -> MagicMock:
    """Create a mock Request for TUS PATCH with body() returning content."""
    mock_request = MagicMock(spec=Request)
    headers = {
        "Content-Type": "application/offset+octet-stream",
        "Upload-Offset": "0",
        "Content-Length": str(len(content)),
    }
    if checksum_header:
        headers["Upload-Checksum"] = checksum_header
    mock_request.headers = headers

    async def _stream():
        yield content

    mock_request.stream = _stream
    return mock_request


def _mock_tus_state(content: bytes) -> dict:
    return {
        "user_id": "user-123",
        "offset": "0",
        "length": str(len(content)),
        "parts": "[]",
        "quarantine_key": "q/k",
        "s3_upload_id": "s3-id",
        "upload_id": "up-id",
        "filename": "f.txt",
        "mime_type": "text/plain",
    }


@pytest.mark.asyncio
async def test_tus_patch_valid_checksum():
    tus_id = "00000000-0000-0000-0000-000000000000"
    content = b"hello world"
    checksum = base64.b64encode(hashlib.sha256(content).digest()).decode()

    mock_request = _mock_tus_request(content, f"sha256 {checksum}")

    mock_user = MagicMock()
    mock_user.id = "user-123"

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.hgetall.return_value = _mock_tus_state(content)
    mock_redis.incr.return_value = 1
    mock_redis.decr.return_value = 0

    with (
        patch("app.routers.tus.upload_part", new_callable=AsyncMock) as m_upload,
        patch("app.routers.tus.complete_multipart_upload", new_callable=AsyncMock),
        patch("app.routers.tus.abort_multipart_upload", new_callable=AsyncMock),
        patch("app.routers.tus._enqueue_processing", new_callable=AsyncMock),
    ):
        m_upload.return_value = "etag-123"

        import uuid

        response = await tus_patch(
            uuid.UUID(tus_id), mock_request, mock_user, mock_redis, AsyncMock()
        )
        assert response.status_code == 204
        assert response.headers["Upload-Offset"] == str(len(content))


@pytest.mark.asyncio
async def test_tus_patch_wrong_checksum():
    tus_id = "00000000-0000-0000-0000-000000000000"
    content = b"hello world"
    wrong_checksum = base64.b64encode(hashlib.sha256(b"wrong").digest()).decode()

    mock_request = _mock_tus_request(content, f"sha256 {wrong_checksum}")

    mock_user = MagicMock()
    mock_user.id = "user-123"

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.hgetall.return_value = _mock_tus_state(content)
    mock_redis.incr.return_value = 1
    mock_redis.decr.return_value = 0

    from app.core.exceptions import AppError

    with (
        patch("app.routers.tus.upload_part", new_callable=AsyncMock) as m_upload,
        patch("app.routers.tus.complete_multipart_upload", new_callable=AsyncMock),
        patch("app.routers.tus.abort_multipart_upload", new_callable=AsyncMock),
    ):
        m_upload.return_value = "etag-123"

        with pytest.raises(AppError) as exc:
            import uuid

            await tus_patch(uuid.UUID(tus_id), mock_request, mock_user, mock_redis, AsyncMock())
        assert exc.value.status_code == 460
