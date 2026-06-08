import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dead_letter import DeadLetterJob
from app.models.upload import Upload
from app.models.user import User, UserRole
from app.workers.upload.repository import UploadWorkerRepository


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


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


@pytest.mark.asyncio
async def test_checkpoint_stage_and_get(db_session: AsyncSession):
    """Test that _checkpoint_stage and _get_pipeline_stage work with the DB."""
    upload_id = str(uuid.uuid4())
    user = await _create_user(db_session)
    upload_row = Upload(
        upload_id=upload_id,
        user_id=user.id,
        quarantine_key=f"quarantine/{user.id}/{upload_id}/test.pdf",
        filename="test.pdf",
        mime_type="application/pdf",
        size_bytes=100,
        status="pending",
    )
    db_session.add(upload_row)
    await db_session.commit()

    from app.workers.upload.context import WorkerContext

    @asynccontextmanager
    async def mock_sessionmaker():
        yield db_session

    ctx = WorkerContext(redis=AsyncMock(), db_sessionmaker=mock_sessionmaker)
    repo = UploadWorkerRepository(ctx)

    stage = await repo.get_pipeline_stage(upload_id)
    assert stage == 0

    await repo.checkpoint_pipeline_stage(upload_id, 2)
    stage = await repo.get_pipeline_stage(upload_id)
    assert stage == 2


@pytest.mark.asyncio
async def test_cancel_upload_endpoint(
    client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
):
    """Test DELETE /api/upload/{upload_id} correctly sets the cancellation flag."""
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())

    with patch("app.routers.upload.status.delete_object", new_callable=AsyncMock):
        mock_redis.zrange.return_value = []
        response = await client.delete(f"/api/upload/{upload_id}", headers=_auth_headers(user))
        assert response.status_code == 204

        cancel_key = f"upload:cancel:{upload_id}"
        mock_redis.set.assert_awaited_with(cancel_key, "1", ex=3600)


@pytest.mark.asyncio
async def test_dead_letter_queue_endpoints(
    client: AsyncClient, db_session: AsyncSession, mock_arq_pool: AsyncMock
):
    """Test GET /api/admin/dlq, and the retry/dismiss actions."""
    admin_user = await _create_user(db_session, role=UserRole.VIEUX)
    await db_session.commit()

    job_id = uuid.uuid4()
    dlq_job = DeadLetterJob(
        id=job_id,
        job_name="process_upload",
        upload_id="test_upload_id",
        payload={"foo": "bar"},
        error_detail="A test error",
        attempts=3,
    )
    db_session.add(dlq_job)
    await db_session.commit()

    response = await client.get("/api/admin/dlq", headers=_auth_headers(admin_user))
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["upload_id"] == "test_upload_id"

    response = await client.post(
        f"/api/admin/dlq/{job_id}/dismiss", headers=_auth_headers(admin_user)
    )
    assert response.status_code == 200

    await db_session.refresh(dlq_job)
    assert dlq_job.resolved_at is not None


@pytest.mark.asyncio
async def test_dead_letter_queue_retry(client: AsyncClient, db_session: AsyncSession):
    """Test POST /api/admin/dlq/{id}/retry re-enqueues the job and marks it resolved."""
    from unittest.mock import AsyncMock, patch

    admin_user = await _create_user(db_session, role=UserRole.VIEUX)
    await db_session.commit()

    job_id = uuid.uuid4()
    dlq_job = DeadLetterJob(
        id=job_id,
        job_name="process_upload",
        upload_id="retry_upload_id",
        payload={
            "user_id": "u1",
            "upload_id": "retry_upload_id",
            "quarantine_key": "q/k",
            "original_filename": "f.pdf",
            "mime_type": "application/pdf",
        },
        error_detail="Some error",
        attempts=3,
    )
    db_session.add(dlq_job)
    await db_session.commit()

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock(return_value=None)

    with patch("app.core.redis.arq_pool", mock_pool):
        response = await client.post(
            f"/api/admin/dlq/{job_id}/retry",
            headers=_auth_headers(admin_user),
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

    mock_pool.enqueue_job.assert_awaited_once_with(
        "process_upload",
        user_id="u1",
        upload_id="retry_upload_id",
        quarantine_key="q/k",
        original_filename="f.pdf",
        mime_type="application/pdf",
    )

    await db_session.refresh(dlq_job)
    assert dlq_job.resolved_at is not None
