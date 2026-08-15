import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.security import create_access_token
from app.models.cas_staging_claim import CasStagingClaim
from app.models.outbox import OutboxJob
from app.models.upload import Upload
from app.models.user import User, UserRole
from app.routers.upload.helpers import (
    _QUOTA_KEY_PREFIX,
    _STATUS_CACHE_PREFIX,
    _UPLOAD_INTENT_PREFIX,
)


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
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_storage_audit():
    with (
        patch(
            "app.routers.upload.presigned.complete_multipart_verified", new_callable=AsyncMock
        ) as m_complete,
        patch("app.core.storage.facade.read_object_bytes", new_callable=AsyncMock) as m_read,
        patch("app.core.storage.facade.delete_object", new_callable=AsyncMock) as m_delete,
        patch("app.routers.upload.status.delete_object", new_callable=AsyncMock) as m_delete_status,
        patch("app.routers.upload.presigned.get_object_info", new_callable=AsyncMock) as m_info,
    ):
        m_info.return_value = {"size": 1024, "content_type": "application/octet-stream"}
        yield {
            "complete": m_complete,
            "read": m_read,
            "delete": m_delete,
            "delete_status": m_delete_status,
            "info": m_info,
        }


@pytest.mark.asyncio
async def test_presigned_multipart_complete_mime_revalidation(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
    mock_storage_audit,
    mock_arq_pool,
):
    user = await _create_user(db_session)
    await db_session.commit()

    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/test.txt"

    intent = {
        "user_id": str(user.id),
        "upload_id": upload_id,
        "quarantine_key": quarantine_key,
        "s3_multipart_id": "s3_test_id",
        "filename": "test.txt",
        "mime_type": "text/plain",
        "size": 1024,
    }
    await fake_redis_setup.set(f"{_UPLOAD_INTENT_PREFIX}{upload_id}", json.dumps(intent))

    # Mock read_object_bytes to return fake PDF magic bytes
    mock_storage_audit["read"].return_value = b"%PDF-1.4"

    headers = _auth_headers(user)
    response = await client.post(
        "/api/upload/presigned-multipart/complete",
        headers=headers,
        json={"upload_id": upload_id, "parts": [{"PartNumber": 1, "ETag": "test"}]},
    )

    assert response.status_code == 202
    assert (
        response.json()["mime_type"] == "application/pdf"
    )  # Assuming guess_mime_from_bytes returns this
    mock_storage_audit["read"].assert_called_once_with(quarantine_key, byte_count=2048)


@pytest.mark.asyncio
async def test_presigned_multipart_abort_cleans_db_and_quota(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/test.txt"

    intent = {
        "user_id": str(user.id),
        "upload_id": upload_id,
        "quarantine_key": quarantine_key,
        "s3_multipart_id": "s3_test_id",
        "filename": "test.txt",
        "mime_type": "text/plain",
        "size": 1024,
    }
    await fake_redis_setup.set(f"{_UPLOAD_INTENT_PREFIX}{upload_id}", json.dumps(intent))
    await fake_redis_setup.zadd(f"{_QUOTA_KEY_PREFIX}{user.id}", {quarantine_key: time.time()})

    up = Upload(
        upload_id=upload_id,
        user_id=user.id,
        quarantine_key=quarantine_key,
        filename="test.txt",
        mime_type="text/plain",
        size_bytes=1024,
        status="pending",
    )
    db_session.add(up)
    await db_session.commit()

    with (
        patch(
            "app.routers.upload.presigned.abort_multipart_upload", new_callable=AsyncMock
        ) as m_abort,
        patch("app.routers.upload.presigned.delete_object", new_callable=AsyncMock),
    ):
        headers = _auth_headers(user)
        response = await client.delete(
            f"/api/upload/presigned-multipart/{upload_id}", headers=headers
        )
        assert response.status_code == 204
        m_abort.assert_called_once()

    # Check quota removed
    quota_len = await fake_redis_setup.zcard(f"{_QUOTA_KEY_PREFIX}{user.id}")
    assert quota_len == 0

    # Check DB status is cancelled
    await db_session.refresh(up)
    assert up.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_upload_finds_uploads_prefix(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup, mock_storage_audit
):
    user = await _create_user(db_session)

    upload_id = str(uuid.uuid4())
    final_key = f"uploads/{user.id}/{upload_id}/test.txt"

    up = Upload(
        upload_id=upload_id,
        user_id=user.id,
        filename="test.txt",
        mime_type="text/plain",
        size_bytes=100,
        quarantine_key=final_key,
        status="pending",
    )
    db_session.add(up)
    await db_session.commit()

    await fake_redis_setup.zadd(f"{_QUOTA_KEY_PREFIX}{user.id}", {final_key: time.time()})

    headers = _auth_headers(user)
    response = await client.delete(f"/api/upload/{upload_id}", headers=headers)
    assert response.status_code == 204

    mock_storage_audit["delete_status"].assert_called_once_with(final_key)
    quota_len = await fake_redis_setup.zcard(f"{_QUOTA_KEY_PREFIX}{user.id}")
    assert quota_len == 0


@pytest.mark.asyncio
async def test_batch_upload_status_multiple_keys(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    await db_session.commit()

    k1 = f"uploads/{user.id}/1/a.txt"
    k2 = f"quarantine/{user.id}/2/b.txt"
    k3 = f"uploads/{user.id}/3/c.txt"  # Not found

    await fake_redis_setup.set(
        f"{_STATUS_CACHE_PREFIX}{k1}", json.dumps({"status": "clean", "file_key": k1})
    )
    await fake_redis_setup.set(
        f"{_STATUS_CACHE_PREFIX}{k2}", json.dumps({"status": "processing", "file_key": k2})
    )

    headers = _auth_headers(user)
    response = await client.post(
        "/api/upload/status/batch", headers=headers, json={"file_keys": [k1, k2, k3, "invalid/key"]}
    )
    assert response.status_code == 200

    data = response.json()["statuses"]
    assert k1 in data
    assert data[k1]["status"] == "clean"
    assert k2 in data
    assert data[k2]["status"] == "processing"
    assert k3 in data
    assert data[k3]["status"] == "pending"
    assert "invalid/key" not in data


@pytest.mark.integration  # needs a live S3/MinIO endpoint
@pytest.mark.asyncio
async def test_stale_pending_upload_cleanup(db_session: AsyncSession, fake_redis_setup):
    user = await _create_user(db_session)

    # Needs a 3-hour old pending upload
    up1 = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=user.id,
        quarantine_key="q1",
        filename="1.txt",
        mime_type="text/plain",
        size_bytes=100,
        status="pending",
        created_at=datetime.now(UTC) - timedelta(hours=3),
    )
    up2 = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=user.id,
        quarantine_key="q2",
        filename="2.txt",
        mime_type="text/plain",
        size_bytes=100,
        status="pending",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add_all([up1, up2])
    await db_session.commit()

    import app.core.database.database as database
    from app.workers.cleanup_uploads import cleanup_uploads

    mock_s3 = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_s3)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    # Properly mock the paginator to avoid infinite loops or hangs
    # aioboto3: get_paginator is sync, returns a sync object with an async paginate() method.
    mock_paginator = MagicMock()
    mock_async_iter = AsyncMock()
    mock_async_iter.__aiter__.return_value = []
    mock_paginator.paginate.return_value = mock_async_iter

    # Force it to be a sync MagicMock so it doesn't return a coroutine
    mock_s3.get_paginator = MagicMock(return_value=mock_paginator)

    async def mock_list_multipart():
        if False:
            yield

    with (
        patch(
            "app.workers.cleanup_uploads.async_session_factory",
            database.async_session_factory,
        ),
        patch("app.workers.cleanup_uploads.get_s3_client", return_value=mock_cm),
        patch("app.workers.cleanup_uploads.list_multipart_uploads", mock_list_multipart),
        patch("app.core.storage.facade.object_exists", AsyncMock(return_value=True)),
        patch("app.workers.storage_ops.delete_storage_objects", AsyncMock()),
        patch("app.workers.cleanup_uploads.reconcile_cas_storage_usage", new_callable=AsyncMock),
    ):
        await cleanup_uploads({"redis": fake_redis_setup})

    await db_session.refresh(up1)
    await db_session.refresh(up2)
    assert up1.status == "failed"
    assert up2.status == "pending"


@pytest.mark.asyncio
async def test_cleanup_expired_cas_staging_claim_releases_once(
    db_session: AsyncSession,
    fake_redis_setup,
    mock_redis: AsyncMock,
) -> None:
    """Only an expired, unconsumed claim owns a CAS reference to release."""
    import app.core.database.database as database
    from app.workers.cleanup_uploads import cleanup_uploads

    user = await _create_user(db_session)
    now = datetime.now(UTC)
    expired_id = uuid.uuid4()
    consumed_id = uuid.uuid4()
    expired = CasStagingClaim(
        id=expired_id,
        user_id=user.id,
        file_key="cas/expired",
        sha256="a" * 64,
        expires_at=now - timedelta(minutes=1),
    )
    consumed = CasStagingClaim(
        id=consumed_id,
        user_id=user.id,
        file_key="cas/consumed",
        sha256="b" * 64,
        expires_at=now + timedelta(days=1),
        consumed_at=now - timedelta(days=8),
    )
    db_session.add_all([expired, consumed])
    await db_session.commit()
    # cleanup_uploads uses a separate session. Keep this fixture session from
    # participating in SQLAlchemy's in-memory synchronization of the bulk DELETE
    # (SQLite drops timezone information when materializing DateTime values).
    db_session.expunge_all()
    mock_redis.scan_iter = fake_redis_setup.scan_iter

    mock_s3 = MagicMock()
    mock_s3.get_paginator = MagicMock()
    paginator = MagicMock()

    async def empty_pages():
        if False:
            yield {}

    paginator.paginate.side_effect = lambda **_kwargs: empty_pages()
    mock_s3.get_paginator.return_value = paginator
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_s3)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    async def empty_multipart_uploads():
        if False:
            yield {}

    from sqlalchemy import delete as sqlalchemy_delete

    def sqlite_safe_delete(model):
        # PostgreSQL stores timezone-aware values. SQLite does not, so disable
        # ORM-side evaluation of the bulk-delete predicate in this test backend.
        return sqlalchemy_delete(model).execution_options(synchronize_session=False)

    with (
        patch(
            "app.workers.cleanup_uploads.async_session_factory",
            database.async_session_factory,
        ),
        patch("app.workers.cleanup_uploads.get_s3_client", return_value=mock_cm),
        patch(
            "app.workers.cleanup_uploads.list_multipart_uploads",
            empty_multipart_uploads,
        ),
        patch(
            "app.workers.cleanup_uploads.dispatch_post_commit_actions",
            new_callable=AsyncMock,
        ),
        patch("app.workers.cleanup_uploads.delete", side_effect=sqlite_safe_delete),
        patch("app.workers.storage_ops.delete_storage_objects", new_callable=AsyncMock),
        patch(
            "app.workers.cleanup_uploads.reconcile_cas_storage_usage",
            new_callable=AsyncMock,
        ) as reconcile_usage,
    ):
        await cleanup_uploads({"redis": mock_redis})

    reconcile_usage.assert_awaited_once_with(mock_redis)
    db_session.expire_all()
    claims = list((await db_session.scalars(select(CasStagingClaim))).all())
    jobs = list((await db_session.scalars(select(OutboxJob))).all())

    assert claims == []
    assert len(jobs) == 1
    assert jobs[0].job_name == "release_cas_references"
    assert jobs[0].args == [
        [
            {
                "sha256": "a" * 64,
                "operation_id": f"qcm-claim:{expired_id}:expire",
            }
        ]
    ]


# ── CSP s3_domain helper ──────────────────────────────────────────────────────


def test_s3_csp_domain_strips_scheme() -> None:
    """_s3_csp_domain must strip the scheme prefix and return the bare hostname."""
    from app.main import _s3_csp_domain

    with patch("app.main.settings") as mock_settings:
        mock_settings.s3_public_endpoint = "https://files.example.com"
        assert _s3_csp_domain() == "files.example.com"


def test_s3_csp_domain_passthrough_when_no_scheme() -> None:
    """_s3_csp_domain returns the value unchanged when there is no scheme."""
    from app.main import _s3_csp_domain

    with patch("app.main.settings") as mock_settings:
        mock_settings.s3_public_endpoint = "files.example.com"
        assert _s3_csp_domain() == "files.example.com"


def test_s3_csp_domain_empty_when_not_configured() -> None:
    """_s3_csp_domain returns an empty string when s3_public_endpoint is unset."""
    from app.main import _s3_csp_domain

    with patch("app.main.settings") as mock_settings:
        mock_settings.s3_public_endpoint = None
        assert _s3_csp_domain() == ""
