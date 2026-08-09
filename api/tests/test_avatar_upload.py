import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.security import create_access_token
from app.models.upload import Upload
from app.models.user import User, UserRole


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="avatar_tester@example.com",
        display_name="Avatar Tester",
        role=UserRole.STUDENT,
        onboarded=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


def auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_avatar_upload_flow(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    mock_arq_pool: AsyncMock,
):
    # 1. Simulate a successful upload to quarantine
    quarantine_key = f"quarantine/{test_user.id}/{uuid.uuid4()}/avatar.png"
    final_key = f"cas/{uuid.uuid4().hex}"
    upload = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=test_user.id,
        quarantine_key=quarantine_key,
        final_key=final_key,
        filename="avatar.png",
        status="clean",
        mime_type="image/png",
        size_bytes=1024,
    )
    db_session.add(upload)
    await db_session.commit()

    # 2. Mock storage and processing
    with (
        patch("app.services.user.download_file_raw", new_callable=AsyncMock) as mock_download,
        patch("app.services.user.upload_file", new_callable=AsyncMock) as mock_upload,
        patch("app.services.user.delete_object", new_callable=AsyncMock) as mock_delete,
        patch("app.services.user.process_avatar_isolated", new_callable=AsyncMock) as mock_process,
    ):
        # Create a real dummy file to be "processed"
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".webp", delete=False) as tf:
            tf.write(b"dummy webp content")
            dummy_processed_path = Path(tf.name)

        mock_process.return_value = dummy_processed_path

        try:
            # 3. Call PATCH /api/users/me with the quarantine key
            response = await client.patch(
                "/api/users/me",
                json={"avatar_url": quarantine_key},
                headers=auth_headers(test_user),
            )
        finally:
            if dummy_processed_path.exists():
                dummy_processed_path.unlink()

    # 4. Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["avatar_url"].startswith("avatars/")
    assert data["avatar_url"].endswith(".webp")

    # Verify storage calls
    mock_download.assert_awaited_once_with(
        final_key,
        mock_download.call_args.args[1],
        max_bytes=20 * 1024 * 1024,
    )
    mock_upload.assert_called_once()
    # Cleanup is transactionally queued and only dispatched after the user row commits.
    mock_delete.assert_not_awaited()
    assert any(
        call.args[:2] == ("delete_storage_objects", [quarantine_key])
        for call in mock_arq_pool.enqueue_job.await_args_list
    )


@pytest.mark.asyncio
async def test_avatar_replacement_removes_new_object_on_transaction_rollback(
    db_session: AsyncSession,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.database.post_commit import (
        PostCommitKey,
        rollback_transaction_callbacks,
    )
    from app.services.user import update_user_profile

    quarantine_key = f"quarantine/{test_user.id}/{uuid.uuid4()}/avatar.png"
    db_session.add(
        Upload(
            upload_id=str(uuid.uuid4()),
            user_id=test_user.id,
            quarantine_key=quarantine_key,
            final_key=f"cas/{uuid.uuid4().hex}",
            filename="avatar.png",
            status="clean",
            mime_type="image/png",
            size_bytes=1024,
        )
    )
    await db_session.flush()
    db_session.info[PostCommitKey.JOBS] = []
    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    async def download(_key: str, path: Path, *, max_bytes: int) -> None:
        assert max_bytes == 20 * 1024 * 1024
        path.write_bytes(b"sanitized image")

    delete = AsyncMock()
    processing_root = tmp_path / "processing"
    monkeypatch.setattr("app.config.settings.processing_root", str(processing_root))

    async def process(path: Path) -> bytes:
        assert path.is_relative_to(processing_root)
        return b"webp"

    with (
        patch("app.services.user.download_file_raw", side_effect=download),
        patch("app.services.user.upload_file", new_callable=AsyncMock) as upload,
        patch("app.services.user.delete_object", delete),
        patch(
            "app.services.user.process_avatar_isolated",
            side_effect=process,
        ),
    ):
        await update_user_profile(db_session, test_user, avatar_url=quarantine_key)
        new_key = upload.await_args.args[1]
        await rollback_transaction_callbacks(db_session)

    delete.assert_awaited_once_with(new_key)


@pytest.mark.asyncio
async def test_avatar_upload_unauthorized(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    # 1. Create an upload belonging to ANOTHER user
    other_user_id = uuid.uuid4()
    quarantine_key = f"quarantine/{other_user_id}/{uuid.uuid4()}/avatar.png"
    upload = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=other_user_id,
        quarantine_key=quarantine_key,
        filename="avatar.png",
        status="clean",
        mime_type="image/png",
        size_bytes=1024,
    )
    db_session.add(upload)
    await db_session.commit()

    # 2. Try to use this key for test_user
    response = await client.patch(
        "/api/users/me", json={"avatar_url": quarantine_key}, headers=auth_headers(test_user)
    )

    # 3. Assertions
    assert response.status_code == 400
    assert "Invalid avatar upload key" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "mime_type", "final_key"),
    [
        ("pending", "image/png", "cas/image"),
        ("malicious", "image/png", "cas/image"),
        ("clean", "application/pdf", "cas/pdf"),
        ("clean", "image/png", None),
    ],
)
async def test_avatar_rejects_unprocessed_or_non_image_upload(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    status: str,
    mime_type: str,
    final_key: str | None,
):
    quarantine_key = f"quarantine/{test_user.id}/{uuid.uuid4()}/avatar.png"
    db_session.add(
        Upload(
            upload_id=str(uuid.uuid4()),
            user_id=test_user.id,
            quarantine_key=quarantine_key,
            final_key=final_key,
            filename="avatar.png",
            status=status,
            mime_type=mime_type,
            size_bytes=1024,
        )
    )
    await db_session.commit()

    response = await client.patch(
        "/api/users/me", json={"avatar_url": quarantine_key}, headers=auth_headers(test_user)
    )

    assert response.status_code == 400
    assert "security processing" in response.json()["detail"]
