from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

from app.core.upload_errors import ERR_TUS_CONCURRENCY_LIMIT
from app.routers.tus import tus_patch


@pytest.mark.asyncio
async def test_tus_concurrency_cap_enforced():
    tus_id = "00000000-0000-0000-0000-000000000000"
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {
        "Content-Type": "application/offset+octet-stream",
        "Upload-Offset": "0",
    }

    mock_user = MagicMock()
    mock_user.id = "user-123"

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.hgetall.return_value = {"offset": "0"}
    # Mock INCR to return a value higher than the limit (8)
    mock_redis.incr.return_value = 10
    mock_redis.decr.return_value = 0

    import uuid

    response = await tus_patch(uuid.UUID(tus_id), mock_request, mock_user, mock_redis, AsyncMock())

    assert response.status_code == 429
    assert response.headers["X-Lectern-Error"] == ERR_TUS_CONCURRENCY_LIMIT
    # Should have decremented after seeing it's too high
    mock_redis.decr.assert_called_with(f"tus:inflight:{mock_user.id}")
