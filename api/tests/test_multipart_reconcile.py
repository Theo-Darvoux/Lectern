from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.reconcile_multipart import reconcile_multipart_uploads


@pytest.mark.asyncio
async def test_reconcile_aborts_orphan():
    # 1. Mock Redis scan: no active tus sessions
    mock_redis = AsyncMock()
    mock_redis.scan.return_value = (0, [])

    # 2. Mock DB session: no processing uploads
    mock_session = AsyncMock()
    mock_session.scalars.return_value = []
    mock_session_factory = MagicMock(return_value=mock_session)

    # 3. Mock S3 listing: one orphan
    initiated = datetime.now(UTC) - timedelta(hours=3)
    orphan = {"UploadId": "s3-orphan", "Key": "quarantine/u1/up1/f.txt", "Initiated": initiated}

    async def mock_list_multipart(prefix):
        yield orphan

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
        m_abort.assert_called_once_with("quarantine/u1/up1/f.txt", "s3-orphan")


@pytest.mark.asyncio
async def test_reconcile_skips_active_tus():
    # 1. Mock Redis: one active tus session for this s3_id
    mock_redis = AsyncMock()
    mock_redis.smembers.return_value = [b"abc"]
    mock_redis.hget.return_value = b"s3-active"

    # 2. Mock DB session: empty
    mock_session = AsyncMock()
    mock_session.scalars.return_value = []
    mock_session_factory = MagicMock(return_value=mock_session)

    # 3. Mock S3: one multipart matching active s3_id
    initiated = datetime.now(UTC) - timedelta(hours=3)
    active = {"UploadId": "s3-active", "Key": "quarantine/u1/up1/f.txt", "Initiated": initiated}

    async def mock_list_multipart(prefix):
        yield active

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
        m_abort.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_skips_recent():
    mock_redis = AsyncMock()
    mock_redis.scan.return_value = (0, [])
    mock_session_factory = MagicMock()

    # Initiated 30 mins ago (< 2 hours)
    initiated = datetime.now(UTC) - timedelta(minutes=30)
    recent = {"UploadId": "s3-recent", "Key": "quarantine/u1/up1/f.txt", "Initiated": initiated}

    async def mock_list_multipart(prefix):
        yield recent

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
        m_abort.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_fails_closed_when_db_query_fails():
    mock_redis = AsyncMock()
    mock_redis.smembers.return_value = []
    mock_session = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_session.scalars.side_effect = RuntimeError("database unavailable")
    mock_session_factory = MagicMock(return_value=mock_session)
    list_uploads = MagicMock()

    with (
        patch("app.workers.reconcile_multipart.list_multipart_uploads", list_uploads),
        patch(
            "app.workers.reconcile_multipart.abort_multipart_upload", new_callable=AsyncMock
        ) as abort,
    ):
        await reconcile_multipart_uploads(
            {"redis": mock_redis, "db_sessionmaker": mock_session_factory}
        )

    list_uploads.assert_not_called()
    abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_preserves_pending_upload():
    mock_redis = AsyncMock()
    mock_redis.smembers.return_value = []
    mock_session = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_session.scalars.return_value = ["up-pending"]
    mock_session_factory = MagicMock(return_value=mock_session)
    initiated = datetime.now(UTC) - timedelta(hours=3)

    async def mock_list_multipart(prefix):
        yield {
            "UploadId": "s3-pending",
            "Key": "quarantine/u1/up-pending/f.txt",
            "Initiated": initiated,
        }

    with (
        patch(
            "app.workers.reconcile_multipart.list_multipart_uploads",
            side_effect=mock_list_multipart,
        ),
        patch(
            "app.workers.reconcile_multipart.abort_multipart_upload", new_callable=AsyncMock
        ) as abort,
    ):
        await reconcile_multipart_uploads(
            {"redis": mock_redis, "db_sessionmaker": mock_session_factory}
        )

    abort.assert_not_awaited()
