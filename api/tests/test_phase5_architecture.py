"""Tests for Phase 5 Architecture improvements.

Covers:
- 3.4: Redis degradation — quota check falls back to DB count on Redis failure
- 3.5: CAS dual-write — _update_db_status receives cas_key + cas_ref_count
- 3.6: Mandatory Upload DB row — _create_upload_row raises on DB failure
- 3.8: Status-based cleanup — query DB for terminal uploads (no S3 scan)
- 3.10: Webhook retry — exponential backoff via ARQ re-enqueue, DLQ after 3 attempts
- 3.11: Optimistic locking — version_lock on MaterialVersion, conflict detection
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@telecom-sudparis.eu",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


# ── 3.6: Mandatory Upload DB row ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_upload_row_raises_on_db_failure():
    """_create_upload_row must raise (not swallow) DB errors."""
    from app.routers.upload.helpers import _create_upload_row

    with patch("app.routers.upload.helpers.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit.side_effect = RuntimeError("DB unavailable")
        mock_factory.return_value = mock_session

        with pytest.raises(RuntimeError, match="DB unavailable"):
            await _create_upload_row(
                upload_id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                quarantine_key="quarantine/x/y/z.pdf",
                filename="z.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
            )


# ── 3.4: Redis degradation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quota_check_falls_back_to_db_on_redis_failure(mock_redis: AsyncMock):
    """On Redis failure, quota check uses DB count and allows upload below cap."""
    from app.routers.upload.helpers import _check_pending_cap

    user_id = str(uuid.uuid4())

    # Redis raises on every call
    mock_redis.zremrangebyscore.side_effect = ConnectionError("Redis down")

    # DB count returns 0 pending uploads — should succeed (no error raised)
    with patch("app.routers.upload.helpers.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=0)
        mock_factory.return_value = mock_session

        # Should not raise
        await _check_pending_cap(user_id, mock_redis)


@pytest.mark.asyncio
async def test_quota_check_db_fallback_enforces_cap(mock_redis: AsyncMock):
    """On Redis failure + DB shows cap exceeded, upload is rejected."""
    from app.core.exceptions import BadRequestError
    from app.routers.upload.helpers import MAX_PENDING_UPLOADS, _check_pending_cap

    user_id = str(uuid.uuid4())
    mock_redis.zremrangebyscore.side_effect = ConnectionError("Redis down")

    with patch("app.routers.upload.helpers.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # Return count equal to cap
        mock_session.scalar = AsyncMock(return_value=MAX_PENDING_UPLOADS)
        mock_factory.return_value = mock_session

        with pytest.raises(BadRequestError):
            await _check_pending_cap(user_id, mock_redis)


# ── 3.5: CAS dual-write ───────────────────────────────────────────────────────


def test_update_db_status_accepts_cas_fields():
    """UploadWorkerRepository.update_upload_status signature accepts cas_key and cas_ref_count."""
    import inspect

    from app.workers.upload.repository import UploadWorkerRepository

    sig = inspect.signature(UploadWorkerRepository.update_upload_status)
    assert "cas_key" in sig.parameters
    assert "cas_ref_count" in sig.parameters


@pytest.mark.asyncio
async def test_increment_cas_ref_returns_count():
    """_increment_cas_ref returns the new ref count (int)."""
    from app.core.cas import increment_cas_ref as _increment_cas_ref

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=1)

    count = await _increment_cas_ref(mock_redis, "a" * 64)
    assert count == 1


@pytest.mark.asyncio
async def test_increment_cas_ref_returns_0_on_error():
    """_increment_cas_ref returns 0 on Redis error (fail-open)."""
    from app.core.cas import increment_cas_ref as _increment_cas_ref

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))

    count = await _increment_cas_ref(mock_redis, "a" * 64)
    assert count == 0


# ── 3.10: Webhook retry ───────────────────────────────────────────────────────


def test_webhook_backoff_values():
    """Webhook backoff must be (30, 120, 480) seconds."""
    from app.workers.webhook_dispatch import _BACKOFF_SECONDS

    assert _BACKOFF_SECONDS == (30, 120, 480)


@pytest.mark.asyncio
async def test_webhook_reenqueues_on_transient_failure():
    """On 503 response, dispatch_webhook re-enqueues with deferred backoff."""

    from app.workers.webhook_dispatch import dispatch_webhook

    upload_id = str(uuid.uuid4())

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    # Minimal Upload row mock — all fields must be JSON serializable
    row = MagicMock(
        spec=[
            "webhook_url",
            "upload_id",
            "status",
            "final_key",
            "sha256",
            "mime_type",
            "size_bytes",
        ]
    )
    row.webhook_url = "https://example.com/webhook"
    row.upload_id = upload_id
    row.status = "clean"
    row.final_key = "cas/abc"
    row.sha256 = "a" * 64
    row.mime_type = "application/pdf"
    row.size_bytes = 1024
    mock_session.scalar = AsyncMock(return_value=row)

    mock_arq = AsyncMock()

    ctx = {
        "db_sessionmaker": lambda: mock_session,
        "arq": mock_arq,
    }

    # Simulate 503 transient error
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.status_code = 503

    with (
        patch("app.workers.webhook_dispatch.validate_webhook_url", return_value=True),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await dispatch_webhook(ctx, upload_id=upload_id, attempt=1)

    # Should have re-enqueued with attempt=2
    mock_arq.enqueue_job.assert_awaited_once()
    call_kwargs = mock_arq.enqueue_job.await_args.kwargs
    assert call_kwargs["upload_id"] == upload_id
    assert call_kwargs["attempt"] == 2


@pytest.mark.asyncio
async def test_webhook_inserts_dlq_after_max_attempts():
    """After max attempts, dispatch_webhook inserts a dead-letter record."""

    from app.workers.webhook_dispatch import _MAX_ATTEMPTS, dispatch_webhook

    upload_id = str(uuid.uuid4())

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    row = MagicMock(
        spec=[
            "webhook_url",
            "upload_id",
            "status",
            "final_key",
            "sha256",
            "mime_type",
            "size_bytes",
        ]
    )
    row.webhook_url = "https://example.com/webhook"
    row.upload_id = upload_id
    row.status = "clean"
    row.final_key = None
    row.sha256 = None
    row.mime_type = None
    row.size_bytes = None
    mock_session.scalar = AsyncMock(return_value=row)

    mock_arq = AsyncMock()
    mock_insert_dlq = AsyncMock()

    ctx = {
        "db_sessionmaker": lambda: mock_session,
        "arq": mock_arq,
    }

    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.status_code = 503

    with (
        patch("app.workers.webhook_dispatch.validate_webhook_url", return_value=True),
        patch("httpx.AsyncClient") as mock_client_cls,
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.insert_dead_letter",
            mock_insert_dlq,
        ),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        # Final attempt
        await dispatch_webhook(ctx, upload_id=upload_id, attempt=_MAX_ATTEMPTS)

    # Should NOT re-enqueue (max attempts reached)
    mock_arq.enqueue_job.assert_not_awaited()
    # Should insert DLQ
    mock_insert_dlq.assert_awaited_once()
    dlq_kwargs = mock_insert_dlq.await_args
    assert dlq_kwargs is not None
    assert (
        dlq_kwargs.kwargs.get("job_name") == "dispatch_webhook"
        or dlq_kwargs.args[2] == "dispatch_webhook"
    )


# ── 3.11: Optimistic locking ──────────────────────────────────────────────────


def test_material_version_has_version_lock_column():
    """MaterialVersion model must have a version_lock column (default 0)."""
    from app.models.material import MaterialVersion

    col = MaterialVersion.__table__.c.get("version_lock")
    assert col is not None
    assert str(col.type) == "INTEGER"


@pytest.mark.asyncio
async def test_edit_material_conflict_raises_on_version_lock_mismatch(
    db_session: AsyncSession,
):
    """_exec_edit_material raises ConflictError when version_lock mismatches."""
    from app.core.exceptions import ConflictError
    from app.models.directory import Directory
    from app.models.material import Material, MaterialVersion
    from app.models.security import VirusScanResult
    from app.services.pr import _exec_edit_material

    # Create minimal fixtures
    dir_ = Directory(
        id=uuid.uuid4(),
        name="test-dir",
        slug="test-dir",
        type="folder",
    )
    db_session.add(dir_)
    await db_session.flush()

    mat = Material(
        id=uuid.uuid4(),
        directory_id=dir_.id,
        title="Test Material",
        slug="test-material",
        type="document",
        current_version=1,
    )
    db_session.add(mat)
    await db_session.flush()

    # Create an existing version with version_lock=2 (simulating a prior edit)
    mv = MaterialVersion(
        id=uuid.uuid4(),
        material_id=mat.id,
        version_number=1,
        version_lock=2,
        virus_scan_result=VirusScanResult.CLEAN,
    )
    db_session.add(mv)
    await db_session.flush()

    # PR operation references version_lock=0 (stale — a concurrent edit bumped it to 2)
    from app.models.pull_request import PRStatus, PullRequest

    author = await _create_user(db_session)
    await db_session.flush()

    pr = PullRequest(
        id=uuid.uuid4(),
        author_id=author.id,
        title="Conflicting edit",
        payload=[],
        status=PRStatus.APPROVED,
    )
    db_session.add(pr)
    await db_session.flush()

    op = {
        "material_id": str(mat.id),
        "file_key": "uploads/user/upload1/doc.pdf",
        "version_lock": 0,  # stale — expected 2
    }

    with (
        patch("app.services.pr._get_file_info", AsyncMock(return_value={"size": 1024})),
        patch("app.services.pr._resolve_mime_type", AsyncMock(return_value="application/pdf")),
        patch("app.core.storage.copy_object", AsyncMock()),
        pytest.raises(ConflictError),
    ):
        await _exec_edit_material(db_session, op, pr, {})
