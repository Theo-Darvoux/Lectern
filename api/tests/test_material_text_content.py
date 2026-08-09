import gzip
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.post_commit import PostCommitKey, rollback_transaction_callbacks
from app.models.material import Material, MaterialVersion
from app.routers.materials import save_material_text_content
from tests.test_materials import _auth_headers, _create_directory, _create_user


@pytest.mark.asyncio
async def test_get_material_text_content_implicit_gzip(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """
    Test that text content is correctly decompressed even if the DB
    metadata doesn't explicitly flag it as gzip, but the bytes start with 1f 8b.
    """
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)

    # Create a material that looks like Markdown
    material = Material(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        directory_id=directory.id,
        title="Droit Chap 6",
        slug="droit-chap-6",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)

    # Version with text/markdown but content will be gzipped
    version = MaterialVersion(
        id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        material_id=material.id,
        version_number=1,
        file_key="materials/test/droit.md",
        file_name="droit.md",
        file_size=100,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()

    original_text = "# Chapitre 6: Droit\n\nContenu du cours..."
    gzipped_bytes = gzip.compress(original_text.encode("utf-8"))

    # Mock read_full_object to return gzipped bytes
    with patch("app.routers.materials.read_full_object", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = gzipped_bytes

        response = await client.get(
            f"/api/materials/{material.id}/text-content", headers=_auth_headers(user)
        )

        assert response.status_code == 200
        assert response.text == original_text
        assert "text/plain" in response.headers["Content-Type"]


@pytest.mark.asyncio
async def test_save_text_content_removes_object_when_transaction_rolls_back(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Rollback Notes",
        slug="rollback-notes",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/rollback.md",
        file_name="rollback.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()
    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    mock_redis = MagicMock()
    mock_redis.zrem = AsyncMock()
    mock_redis.register_script = MagicMock(return_value=AsyncMock())


    with (
        patch("app.routers.materials.read_full_object", new=AsyncMock(return_value=b"old")),
        patch("app.routers.materials.storage_upload_file", new_callable=AsyncMock),
        patch("app.routers.materials._check_pending_cap", new_callable=AsyncMock),
        patch("app.routers.materials._reserve_storage_limit", new_callable=AsyncMock),
        patch("app.routers.materials._release_storage_reservation", new_callable=AsyncMock),
        patch("app.core.storage.facade.delete_object", new_callable=AsyncMock) as delete_object,
    ):
        result = await save_material_text_content(
            str(material.id), user, db_session, "new contents", redis=mock_redis
        )
        await db_session.rollback()
        await rollback_transaction_callbacks(db_session)

    delete_object.assert_awaited_once_with(result["file_key"])




@pytest.mark.asyncio
async def test_get_material_text_content_plain_text(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test that normal plain text still works."""
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)

    material = Material(
        id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        directory_id=directory.id,
        title="Plain Notes",
        slug="plain-notes",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)

    version = MaterialVersion(
        id=uuid.UUID("00000000-0000-0000-0000-000000000004"),
        material_id=material.id,
        version_number=1,
        file_key="materials/test/plain.md",
        file_name="plain.md",
        file_size=100,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()

    original_text = "Just some plain text."

    with patch("app.routers.materials.read_full_object", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = original_text.encode("utf-8")

        response = await client.get(
            f"/api/materials/{material.id}/text-content", headers=_auth_headers(user)
        )

        assert response.status_code == 200
        assert response.text == original_text
