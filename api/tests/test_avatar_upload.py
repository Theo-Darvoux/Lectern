import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.upload import Upload
from app.models.user import User, UserRole


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="avatar_tester@telecom-sudparis.eu",
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
async def test_avatar_upload_flow(client: AsyncClient, db_session: AsyncSession, test_user: User):
    # 1. Simulate a successful upload to quarantine
    quarantine_key = f"quarantine/{test_user.id}/{uuid.uuid4()}/avatar.png"
    upload = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=test_user.id,
        quarantine_key=quarantine_key,
        filename="avatar.png",
        status="clean",
        mime_type="image/png",
        size_bytes=1024,
    )
    db_session.add(upload)
    await db_session.commit()

    # 2. Mock storage and processing
    with (
        patch("app.services.user.download_file", new_callable=AsyncMock) as mock_download,
        patch("app.services.user.upload_file", new_callable=AsyncMock) as mock_upload,
        patch("app.services.user.delete_object", new_callable=AsyncMock) as mock_delete,
        patch("app.services.user.process_avatar") as mock_process,
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
    mock_download.assert_called_once()
    mock_upload.assert_called_once()
    # Should delete the quarantine file
    mock_delete.assert_any_call(quarantine_key)


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
