"""Transaction-boundary regressions for direct-upload idempotency."""

import io
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.database as database
from app.core.database.post_commit import (
    PostCommitKey,
    finalize_transaction_callbacks,
)
from app.models.user import User, UserRole
from app.routers.upload.direct import (
    _cache_idempotency_after_commit,
    _validated_idempotency_cache_hit,
)
from app.schemas.material import UploadPendingOut, UploadStatus


def _pending(upload_id: str = "11111111-1111-1111-1111-111111111111") -> UploadPendingOut:
    return UploadPendingOut(
        upload_id=upload_id,
        file_key=f"quarantine/user/{upload_id}/file.txt",
        status=UploadStatus.PENDING,
        size=5,
        mime_type="text/plain",
    )


async def _create_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        display_name="Idempotency tester",
        role=UserRole.STUDENT,
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
@patch("app.routers.upload.direct.get_s3_client")
async def test_direct_upload_commit_failure_does_not_leave_authoritative_idempotency_success(
    mock_s3_cm,
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    """A failed request COMMIT cannot make Redis authoritative for the retry."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3
    user = await _create_user(db_session)
    upload_id = str(uuid.uuid4())
    headers = {**_auth_headers(user), "X-Upload-ID": upload_id}

    async def fail_commit(_self: AsyncSession) -> None:
        raise ConnectionError("commit acknowledgement lost")

    with patch.object(AsyncSession, "commit", new=fail_commit):
        try:
            first_response = await client.post(
                "/api/upload",
                files={
                    "file": (
                        "test.pdf",
                        io.BytesIO(b"%PDF-1.4 idempotency commit failure"),
                        "application/pdf",
                    )
                },
                headers=headers,
            )
        except ConnectionError as exc:
            assert "commit acknowledgement lost" in str(exc)
        else:
            # Some ASGI transports surface dependency-finalization failures as
            # a 5xx response instead of re-raising the application exception.
            assert first_response.status_code >= 500

    # The transaction did not commit, and the post-commit publisher never ran.
    idem_key = f"upload:idem:{user.id}:{upload_id}"
    assert not any(
        call.args and call.args[0] == idem_key for call in mock_redis.set.await_args_list
    )

    # Restore normal COMMIT semantics and retry the exact request identity. The
    # retry must execute the upload again, not return a phantom cached 202.
    response = await client.post(
        "/api/upload",
        files={
            "file": (
                "test.pdf",
                io.BytesIO(b"%PDF-1.4 idempotency commit failure"),
                "application/pdf",
            )
        },
        headers=headers,
    )
    assert response.status_code == 202
    assert response.json()["upload_id"] == upload_id
    assert mock_s3.upload_file.await_count == 2
    assert any(call.args and call.args[0] == idem_key for call in mock_redis.set.await_args_list)


@pytest.mark.asyncio
async def test_direct_upload_commit_callback_is_not_published_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.info = {}
    session.commit = AsyncMock(side_effect=ConnectionError("commit acknowledgement lost"))
    session.rollback = AsyncMock()

    @asynccontextmanager
    async def fake_factory():
        yield session

    monkeypatch.setattr(database, "async_session_factory", fake_factory)

    redis = AsyncMock()
    result = _pending()
    dependency = database.get_db()
    managed = await anext(dependency)

    assert _cache_idempotency_after_commit(managed, redis, "upload:idem:user:key", result)
    redis.set.assert_not_awaited()

    with pytest.raises(ConnectionError, match="commit acknowledgement lost"):
        await anext(dependency)

    # get_db deliberately preserves external resources after an ambiguous COMMIT,
    # but the Redis success publisher is discarded and was never authoritative.
    redis.set.assert_not_awaited()
    assert PostCommitKey.TRANSACTION_COMMIT_CALLBACKS not in session.info


@pytest.mark.asyncio
async def test_direct_upload_cache_is_published_only_after_commit_finalization() -> None:
    db = AsyncMock()
    db.info = {PostCommitKey.MANAGED_TRANSACTION: True}
    redis = AsyncMock()
    result = _pending()

    assert _cache_idempotency_after_commit(db, redis, "upload:idem:user:key", result)
    redis.set.assert_not_awaited()

    await finalize_transaction_callbacks(db)

    redis.set.assert_awaited_once()
    args, kwargs = redis.set.await_args
    assert args[0] == "upload:idem:user:key"
    assert result.upload_id in args[1]
    assert kwargs["ex"] > 0


@pytest.mark.asyncio
async def test_direct_upload_ambiguous_commit_retry_reconciles_db_before_cached_202() -> None:
    expected_upload_id = "11111111-1111-1111-1111-111111111111"
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=_pending(expected_upload_id).model_dump_json())

    result = await _validated_idempotency_cache_hit(
        redis,
        db,
        "upload:idem:user:key",
        "22222222-2222-2222-2222-222222222222",
        expected_upload_id,
    )

    assert result is None
    redis.delete.assert_awaited_once_with("upload:idem:user:key")


@pytest.mark.asyncio
async def test_direct_upload_cache_key_cannot_return_different_upload_id() -> None:
    expected_upload_id = "11111111-1111-1111-1111-111111111111"
    db = AsyncMock()
    redis = AsyncMock()
    redis.get = AsyncMock(
        return_value=_pending("33333333-3333-3333-3333-333333333333").model_dump_json()
    )

    result = await _validated_idempotency_cache_hit(
        redis,
        db,
        "upload:idem:user:key",
        "22222222-2222-2222-2222-222222222222",
        expected_upload_id,
    )

    assert result is None
    db.scalar.assert_not_awaited()
    redis.delete.assert_awaited_once_with("upload:idem:user:key")
