"""Tests for the directory ZIP download feature.

Covers:
- get_directory_download_entries service function (arcname construction, recursion,
  safety limits, deduplication, quarantine filtering)
- GET /api/directories/{id}/download endpoint (auth, streaming ZIP, error cases)
"""

import io
import uuid
import zipfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
from app.models.user import User, UserRole
from app.services.directory import (
    _DOWNLOAD_MAX_BYTES,
    _DOWNLOAD_MAX_FILES,
    get_directory_download_entries,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _create_directory(
    db: AsyncSession,
    user: User,
    parent_id: uuid.UUID | None = None,
    name: str = "Dir",
    slug: str | None = None,
) -> Directory:
    directory = Directory(
        id=uuid.uuid4(),
        parent_id=parent_id,
        name=name,
        slug=slug or name.lower().replace(" ", "-"),
        type="folder",
        created_by=user.id,
    )
    db.add(directory)
    await db.flush()
    return directory


async def _create_material_with_version(
    db: AsyncSession,
    directory: Directory,
    user: User,
    title: str = "Material",
    file_key: str | None = None,
    file_name: str = "file.pdf",
    file_size: int = 1024,
    parent_material_id: uuid.UUID | None = None,
) -> tuple[Material, MaterialVersion]:
    mat = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title=title,
        slug=f"{title.lower().replace(' ', '-')}-{uuid.uuid4().hex[:4]}",
        type="pdf",
        author_id=user.id,
        current_version=1,
        parent_material_id=parent_material_id,
    )
    db.add(mat)
    await db.flush()

    version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=mat.id,
        version_number=1,
        file_key=file_key or f"uploads/{user.id}/{uuid.uuid4().hex}/{file_name}",
        file_name=file_name,
        file_size=file_size,
        file_mime_type="application/pdf",
    )
    db.add(version)
    await db.flush()
    return mat, version


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


def _token(user: User) -> str:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return token


def _make_stream_mock(content: bytes):
    """Return a drop-in replacement for storage.stream_object that yields *content*."""
    mock_body = AsyncMock()
    mock_body.read = AsyncMock(side_effect=[content, b""])
    mock_body.close = AsyncMock()

    @asynccontextmanager
    async def _stream(*_args, **_kwargs):
        yield mock_body

    return _stream


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


class TestGetDirectoryDownloadEntries:
    async def test_empty_directory_returns_no_entries(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user, name="Empty")
        await db_session.commit()

        name, entries = await get_directory_download_entries(db_session, directory.id)
        assert name == "Empty"
        assert entries == []

    async def test_single_file_arcname_is_just_filename(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user, name="Docs")
        _, version = await _create_material_with_version(
            db_session, directory, user, file_name="report.pdf"
        )
        await db_session.commit()

        _, entries = await get_directory_download_entries(db_session, directory.id)
        assert len(entries) == 1
        arcname, file_key = entries[0]
        assert arcname == "report.pdf"
        assert file_key == version.file_key

    async def test_subdirectory_files_get_nested_arcname(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        root = await _create_directory(db_session, user, name="Root")
        sub = await _create_directory(db_session, user, parent_id=root.id, name="Sub")
        await _create_material_with_version(db_session, root, user, file_name="root_file.pdf")
        await _create_material_with_version(db_session, sub, user, file_name="sub_file.pdf")
        await db_session.commit()

        _, entries = await get_directory_download_entries(db_session, root.id)
        arcnames = {a for a, _ in entries}
        assert "root_file.pdf" in arcnames
        assert "Sub/sub_file.pdf" in arcnames

    async def test_deeply_nested_arcname(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        root = await _create_directory(db_session, user, name="Root")
        mid = await _create_directory(db_session, user, parent_id=root.id, name="Mid")
        leaf = await _create_directory(db_session, user, parent_id=mid.id, name="Leaf")
        await _create_material_with_version(db_session, leaf, user, file_name="deep.pdf")
        await db_session.commit()

        _, entries = await get_directory_download_entries(db_session, root.id)
        assert len(entries) == 1
        assert entries[0][0] == "Mid/Leaf/deep.pdf"

    async def test_quarantine_keys_excluded(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        await _create_material_with_version(
            db_session,
            directory,
            user,
            file_key=f"quarantine/{user.id}/abc/scan.pdf",
            file_name="scan.pdf",
        )
        await db_session.commit()

        _, entries = await get_directory_download_entries(db_session, directory.id)
        assert entries == []

    async def test_materials_without_file_key_excluded(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        mat = Material(
            id=uuid.uuid4(),
            directory_id=directory.id,
            title="No File",
            slug="no-file",
            type="pdf",
            author_id=user.id,
            current_version=1,
        )
        db_session.add(mat)
        await db_session.flush()
        version = MaterialVersion(
            id=uuid.uuid4(),
            material_id=mat.id,
            version_number=1,
            file_key=None,
            file_name="ghost.pdf",
            file_size=0,
        )
        db_session.add(version)
        await db_session.commit()

        _, entries = await get_directory_download_entries(db_session, directory.id)
        assert entries == []

    async def test_attachment_materials_nested_under_parent_stem(
        self, db_session: AsyncSession
    ) -> None:
        """Attachments appear under a subfolder named after the parent file stem."""
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        parent_mat, _ = await _create_material_with_version(
            db_session, directory, user, file_name="main.pdf"
        )
        await _create_material_with_version(
            db_session,
            directory,
            user,
            file_name="attachment.pdf",
            parent_material_id=parent_mat.id,
        )
        await db_session.commit()

        _, entries = await get_directory_download_entries(db_session, directory.id)
        arcnames = {a for a, _ in entries}
        assert len(entries) == 2
        assert "main.pdf" in arcnames
        assert "main/attachment.pdf" in arcnames

    async def test_duplicate_filenames_are_deduplicated(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        await _create_material_with_version(
            db_session, directory, user, title="Mat A", file_name="notes.pdf"
        )
        await _create_material_with_version(
            db_session, directory, user, title="Mat B", file_name="notes.pdf"
        )
        await db_session.commit()

        _, entries = await get_directory_download_entries(db_session, directory.id)
        assert len(entries) == 2
        arcnames = {a for a, _ in entries}
        assert len(arcnames) == 2  # both present with distinct names
        assert "notes.pdf" in arcnames
        assert "notes_1.pdf" in arcnames

    async def test_file_count_limit_raises(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        for i in range(_DOWNLOAD_MAX_FILES + 1):
            await _create_material_with_version(
                db_session,
                directory,
                user,
                title=f"Mat {i}",
                file_name=f"file_{i}.pdf",
            )
        await db_session.commit()

        with pytest.raises(ValueError, match="too many files"):
            await get_directory_download_entries(db_session, directory.id)

    async def test_total_size_limit_raises(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        # One material that exceeds the byte limit
        await _create_material_with_version(
            db_session,
            directory,
            user,
            file_name="huge.pdf",
            file_size=_DOWNLOAD_MAX_BYTES + 1,
        )
        await db_session.commit()

        with pytest.raises(ValueError, match="too large"):
            await get_directory_download_entries(db_session, directory.id)

    async def test_nonexistent_directory_raises(self, db_session: AsyncSession) -> None:
        from app.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await get_directory_download_entries(db_session, uuid.uuid4())

    async def test_subdirectory_request_excludes_sibling(self, db_session: AsyncSession) -> None:
        """Downloading a subdirectory must not include files from sibling directories."""
        user = await _create_user(db_session)
        root = await _create_directory(db_session, user, name="Root")
        sub_a = await _create_directory(db_session, user, parent_id=root.id, name="SubA")
        sub_b = await _create_directory(db_session, user, parent_id=root.id, name="SubB")
        await _create_material_with_version(db_session, sub_a, user, file_name="a.pdf")
        await _create_material_with_version(db_session, sub_b, user, file_name="b.pdf")
        await db_session.commit()

        _, entries = await get_directory_download_entries(db_session, sub_a.id)
        arcnames = {a for a, _ in entries}
        assert "a.pdf" in arcnames
        assert not any("b.pdf" in a for a in arcnames)


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------


class TestDownloadDirectoryZipEndpoint:
    async def test_requires_authentication(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        await db_session.commit()

        response = await client.get(f"/api/directories/{directory.id}/download")
        assert response.status_code == 401

    async def test_empty_directory_returns_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        await db_session.commit()

        response = await client.get(
            f"/api/directories/{directory.id}/download",
            headers=_auth_headers(user),
        )
        assert response.status_code == 400

    async def test_nonexistent_directory_returns_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        await db_session.commit()

        response = await client.get(
            f"/api/directories/{uuid.uuid4()}/download",
            headers=_auth_headers(user),
        )
        assert response.status_code == 404

    async def test_returns_valid_zip_with_correct_file(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user, name="MyDir")
        _, version = await _create_material_with_version(
            db_session, directory, user, file_name="report.pdf"
        )
        await db_session.commit()

        file_content = b"PDF content here"
        with patch("app.routers.directories.stream_object", _make_stream_mock(file_content)):
            response = await client.get(
                f"/api/directories/{directory.id}/download",
                headers=_auth_headers(user),
            )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "MyDir.zip" in response.headers["content-disposition"]

        # Parse the returned ZIP and verify contents
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        assert "report.pdf" in names
        assert zf.read("report.pdf") == file_content

    async def test_zip_preserves_subdirectory_structure(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        root = await _create_directory(db_session, user, name="Root")
        sub = await _create_directory(db_session, user, parent_id=root.id, name="Sub")
        await _create_material_with_version(db_session, root, user, file_name="top.pdf")
        await _create_material_with_version(db_session, sub, user, file_name="nested.pdf")
        await db_session.commit()

        root_content = b"root file"
        sub_content = b"nested file"
        call_count = 0

        @asynccontextmanager
        async def _multi_stream(file_key, *args, **kwargs):
            nonlocal call_count
            mock_body = AsyncMock()
            content = root_content if call_count == 0 else sub_content
            call_count += 1
            mock_body.read = AsyncMock(side_effect=[content, b""])
            mock_body.close = AsyncMock()
            yield mock_body

        with patch("app.routers.directories.stream_object", _multi_stream):
            response = await client.get(
                f"/api/directories/{root.id}/download",
                headers=_auth_headers(user),
            )

        assert response.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        assert "top.pdf" in names
        assert "Sub/nested.pdf" in names

    async def test_token_query_param_authenticates(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user, name="TokenDir")
        await _create_material_with_version(db_session, directory, user, file_name="doc.pdf")
        await db_session.commit()

        with patch("app.routers.directories.stream_object", _make_stream_mock(b"data")):
            response = await client.get(
                f"/api/directories/{directory.id}/download",
                params={"token": _token(user)},
            )

        assert response.status_code == 200

    async def test_zip_skips_s3_errors_gracefully(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """If S3 fails for one file, the ZIP is still returned (that file is skipped)."""
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        await _create_material_with_version(
            db_session, directory, user, title="Good", file_name="good.pdf"
        )
        await _create_material_with_version(
            db_session, directory, user, title="Bad", file_name="bad.pdf"
        )
        await db_session.commit()

        call_count = 0

        @asynccontextmanager
        async def _failing_stream(file_key, *args, **kwargs):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                mock_body = AsyncMock()
                mock_body.read = AsyncMock(side_effect=[b"good content", b""])
                mock_body.close = AsyncMock()
                yield mock_body
            else:
                call_count += 1
                raise RuntimeError("S3 unavailable")
                yield  # unreachable, satisfies generator type

        with patch("app.routers.directories.stream_object", _failing_stream):
            response = await client.get(
                f"/api/directories/{directory.id}/download",
                headers=_auth_headers(user),
            )

        assert response.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        # Exactly one file made it
        assert len(names) == 1

    async def test_too_large_directory_returns_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        await _create_material_with_version(
            db_session,
            directory,
            user,
            file_name="huge.pdf",
            file_size=_DOWNLOAD_MAX_BYTES + 1,
        )
        await db_session.commit()

        response = await client.get(
            f"/api/directories/{directory.id}/download",
            headers=_auth_headers(user),
        )
        assert response.status_code == 400
        assert "large" in response.json()["detail"].lower()

    async def test_content_disposition_uses_directory_name(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user, name="Lecture Notes")
        await _create_material_with_version(db_session, directory, user, file_name="slide.pdf")
        await db_session.commit()

        with patch("app.routers.directories.stream_object", _make_stream_mock(b"data")):
            response = await client.get(
                f"/api/directories/{directory.id}/download",
                headers=_auth_headers(user),
            )

        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        assert "Lecture Notes.zip" in disposition

    async def test_rate_limiting_blocks_excessive_requests(
        self, client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
    ) -> None:
        """When the per-minute counter exceeds the limit, download must be rejected."""
        user = await _create_user(db_session)
        directory = await _create_directory(db_session, user)
        await _create_material_with_version(db_session, directory, user, file_name="file.pdf")
        await db_session.commit()

        # Simulate minute counter already at limit + 1 (dev limit is 100, prod is 10)
        mock_redis.pipeline.return_value.__aenter__.return_value.execute = AsyncMock(
            return_value=[101, True, 1, True]
        )

        response = await client.get(
            f"/api/directories/{directory.id}/download",
            headers=_auth_headers(user),
        )
        assert response.status_code == 429
