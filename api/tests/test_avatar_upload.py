import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.security import create_access_token
from app.models.upload import Upload
from app.models.user import User, UserRole
from app.schemas.annotation import AnnotationAuthor
from app.schemas.auth import UserBrief
from app.schemas.comment import CommentAuthor


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
        cas_ref_count=1,
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
            # 3. Adopt the caller-owned upload by opaque upload_id
            response = await client.patch(
                "/api/users/me",
                json={"avatar_upload_id": upload.upload_id},
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
    upload_record = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=test_user.id,
        quarantine_key=quarantine_key,
        final_key=f"cas/{uuid.uuid4().hex}",
        filename="avatar.png",
        status="clean",
        mime_type="image/png",
        size_bytes=1024,
        cas_ref_count=1,
    )
    db_session.add(upload_record)
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
        patch("app.services.user.upload_file", new_callable=AsyncMock) as mock_upload,
        patch("app.services.user.delete_object", delete),
        patch(
            "app.services.user.process_avatar_isolated",
            side_effect=process,
        ),
    ):
        await update_user_profile(db_session, test_user, avatar_upload_id=upload_record.upload_id)
        new_key = mock_upload.await_args.args[1]
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

    # 2. Try to adopt another user's upload_id
    response = await client.patch(
        "/api/users/me",
        json={"avatar_upload_id": upload.upload_id},
        headers=auth_headers(test_user),
    )

    # 3. Assertions
    assert response.status_code == 400
    assert "Invalid avatar upload" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "mime_type", "final_key", "cas_ref_count"),
    [
        ("pending", "image/png", "cas/image", 1),
        ("malicious", "image/png", "cas/image", 1),
        ("clean", "application/pdf", "cas/pdf", 1),
        ("clean", "image/png", None, 0),
        ("clean", "image/png", "cas/image", 0),
    ],
)
async def test_avatar_rejects_unprocessed_or_non_image_upload(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    status: str,
    mime_type: str,
    final_key: str | None,
    cas_ref_count: int,
):
    quarantine_key = f"quarantine/{test_user.id}/{uuid.uuid4()}/avatar.png"
    upload = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=test_user.id,
        quarantine_key=quarantine_key,
        final_key=final_key,
        filename="avatar.png",
        status=status,
        mime_type=mime_type,
        size_bytes=1024,
        cas_ref_count=cas_ref_count,
    )
    db_session.add(upload)
    await db_session.commit()

    response = await client.patch(
        "/api/users/me",
        json={"avatar_upload_id": upload.upload_id},
        headers=auth_headers(test_user),
    )

    assert response.status_code == 400
    assert "security processing" in response.json()["detail"]


@pytest.mark.asyncio
async def test_avatar_rejects_clean_upload_with_wrong_quarantine_lineage(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    other_user_id = uuid.uuid4()
    upload = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=test_user.id,
        quarantine_key=f"quarantine/{other_user_id}/{uuid.uuid4()}/avatar.png",
        final_key=f"cas/{uuid.uuid4().hex}",
        filename="avatar.png",
        status="clean",
        mime_type="image/png",
        size_bytes=1024,
        cas_ref_count=1,
    )
    db_session.add(upload)
    await db_session.commit()

    with patch("app.services.user.download_file_raw", new_callable=AsyncMock) as download:
        response = await client.patch(
            "/api/users/me",
            json={"avatar_upload_id": upload.upload_id},
            headers=auth_headers(test_user),
        )

    assert response.status_code == 400
    assert "security processing" in response.json()["detail"]
    download.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_ref",
    [
        "cas/legacy-private-object",
        "materials/legacy-private-object",
        "quarantine/legacy/private/avatar.png",
        "avatars/00000000-0000-0000-0000-000000000000/00000000-0000-0000-0000-000000000001.webp",
        "https://evil.example/avatar.png",
    ],
)
async def test_public_profile_serializes_unsafe_legacy_avatar_as_null(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    unsafe_ref: str,
) -> None:
    test_user.avatar_url = unsafe_ref
    await db_session.commit()

    response = await client.get(f"/api/users/{test_user.id}", headers=auth_headers(test_user))

    assert response.status_code == 200
    assert response.json()["avatar_url"] is None


@pytest.mark.asyncio
async def test_public_profile_preserves_safe_owned_avatar_reference(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    avatar_key = f"avatars/{test_user.id}/{uuid.uuid4()}.webp"
    test_user.avatar_url = avatar_key
    await db_session.commit()

    response = await client.get(f"/api/users/{test_user.id}", headers=auth_headers(test_user))

    assert response.status_code == 200
    assert response.json()["avatar_url"] == avatar_key


@pytest.mark.parametrize(
    "schema_factory",
    [
        lambda user_id, avatar_url: CommentAuthor(
            id=user_id, display_name="User", avatar_url=avatar_url
        ),
        lambda user_id, avatar_url: AnnotationAuthor(
            id=user_id, display_name="User", avatar_url=avatar_url
        ),
        lambda user_id, avatar_url: UserBrief(
            id=str(user_id),
            email="user@example.com",
            display_name="User",
            avatar_url=avatar_url,
            role="student",
            onboarded=True,
            auto_approve=True,
        ),
    ],
)
def test_common_avatar_output_schemas_sanitize_unsafe_references(schema_factory) -> None:
    user_id = uuid.uuid4()
    model = schema_factory(user_id, "cas/legacy-private-object")
    assert model.model_dump()["avatar_url"] is None


@pytest.mark.asyncio
async def test_avatar_service_rejects_raw_storage_reference(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    from app.core.common.exceptions import BadRequestError
    from app.services.user import update_user_profile

    with pytest.raises(BadRequestError, match="avatar_url is read-only"):
        await update_user_profile(db_session, test_user, avatar_url="cas/victim-staged-object")


@pytest.mark.asyncio
async def test_avatar_http_rejects_raw_storage_reference(
    client: AsyncClient,
    test_user: User,
) -> None:
    response = await client.patch(
        "/api/users/me",
        json={"avatar_url": "cas/victim-staged-object"},
        headers=auth_headers(test_user),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_ref",
    [
        "cas/victim-staged-object",
        "materials/private-object",
        "quarantine/victim/upload/avatar.png",
        "avatars/00000000-0000-0000-0000-000000000000/00000000-0000-0000-0000-000000000001.webp",
        "https://evil.example/avatar.png",
    ],
)
async def test_avatar_endpoint_fails_closed_for_unsafe_legacy_reference(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    unsafe_ref: str,
) -> None:
    test_user.avatar_url = unsafe_ref
    await db_session.commit()

    with patch("app.routers.users.generate_presigned_get", new_callable=AsyncMock) as presign:
        response = await client.get(
            f"/api/users/{test_user.id}/avatar", headers=auth_headers(test_user)
        )

    assert response.status_code == 404
    presign.assert_not_awaited()


@pytest.mark.asyncio
async def test_avatar_endpoint_presigns_only_own_avatar_namespace(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    avatar_key = f"avatars/{test_user.id}/{uuid.uuid4()}.webp"
    test_user.avatar_url = avatar_key
    await db_session.commit()

    with patch(
        "app.routers.users.generate_presigned_get",
        new_callable=AsyncMock,
        return_value="https://storage.example/signed",
    ) as presign:
        response = await client.get(
            f"/api/users/{test_user.id}/avatar",
            headers=auth_headers(test_user),
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "https://storage.example/signed"
    presign.assert_awaited_once_with(avatar_key)


@pytest.mark.asyncio
async def test_avatar_endpoint_redirects_only_trusted_google_avatar(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    google_avatar = "https://lh3.googleusercontent.com/a/example=s96-c"
    test_user.avatar_url = google_avatar
    await db_session.commit()

    with patch("app.routers.users.generate_presigned_get", new_callable=AsyncMock) as presign:
        response = await client.get(
            f"/api/users/{test_user.id}/avatar",
            headers=auth_headers(test_user),
            follow_redirects=False,
        )

    assert response.status_code in (302, 307)
    assert response.headers["location"] == google_avatar
    presign.assert_not_awaited()
