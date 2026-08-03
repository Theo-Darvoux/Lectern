from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from app.core.common.upload_errors import UploadErrorCode
from app.core.database.redis import RedisSemaphoreTimeoutError
from app.routers.tus import tus_patch


@pytest.mark.asyncio
async def test_tus_concurrency_cap_enforced():
    tus_id = "00000000-0000-0000-0000-000000000000"
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        "Content-Type": "application/offset+octet-stream",
        "Upload-Offset": "0",
        "Content-Length": "0",
    }
    mock_user = MagicMock()
    mock_user.id = "user-123"
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with (
        patch(
            "app.routers.tus._load_state",
            new_callable=AsyncMock,
            return_value={"user_id": "user-123", "upload_id": "upload-123"},
        ),
        patch(
            "app.routers.tus.redis_semaphore",
            side_effect=RedisSemaphoreTimeoutError("full"),
        ),
    ):
        import uuid

        response = await tus_patch(
            uuid.UUID(tus_id), mock_request, mock_user, mock_redis, AsyncMock()
        )

    assert response.status_code == 429
    assert response.headers["X-Lectern-Error"] == UploadErrorCode.TUS_CONCURRENCY_LIMIT
