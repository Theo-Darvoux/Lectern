import gzip
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material, MaterialVersion
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
    with patch("app.core.storage.read_full_object", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = gzipped_bytes

        response = await client.get(
            f"/api/materials/{material.id}/text-content", headers=_auth_headers(user)
        )

        assert response.status_code == 200
        assert response.text == original_text
        assert "text/plain" in response.headers["Content-Type"]


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

    with patch("app.core.storage.read_full_object", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = original_text.encode("utf-8")

        response = await client.get(
            f"/api/materials/{material.id}/text-content", headers=_auth_headers(user)
        )

        assert response.status_code == 200
        assert response.text == original_text
