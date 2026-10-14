"""Anonymous pull requests: the author is only ever serialized to moderators."""

import typing
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.schemas.material import MaterialOut, MaterialVersionOut
from app.services.pr_visibility import anonymous_material_key, redact_user_scoped_key


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name=f"User {uuid.uuid4().hex[:4]}",
        role=role,
        onboarded=True,
        gdpr_consent=True,
        auto_approve=False,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def mock_pr_deps(mock_redis: AsyncMock) -> typing.Generator[AsyncMock, None, None]:
    with patch("app.services.pr.object_exists", new_callable=AsyncMock) as m_exists:
        m_exists.return_value = True
        mock_redis.get.return_value = None
        yield m_exists


async def _create_anonymous_pr(client: AsyncClient, author: User) -> dict[str, typing.Any]:
    resp = await client.post(
        "/api/pull-requests",
        json={
            "title": "Anonymous folder",
            "is_anonymous": True,
            "operations": [{"op": "create_directory", "name": "Anon Dir", "type": "folder"}],
        },
        headers=_auth_headers(author),
    )
    assert resp.status_code == 201, resp.text
    return typing.cast(dict[str, typing.Any], resp.json())


class TestAnonymousPRVisibility:
    async def test_other_member_never_receives_author(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        author = await _create_user(db_session)
        other = await _create_user(db_session)
        await db_session.commit()

        pr = await _create_anonymous_pr(client, author)
        resp = await client.get(f"/api/pull-requests/{pr['id']}", headers=_auth_headers(other))

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_anonymous"] is True
        assert body["is_own"] is False
        assert body["author"] is None
        assert body["author_id"] is None
        assert str(author.id) not in resp.text

    async def test_author_sees_own_pr_as_anonymous(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        author = await _create_user(db_session)
        await db_session.commit()

        created = await _create_anonymous_pr(client, author)
        assert created["author"] is None
        assert created["is_own"] is True

        resp = await client.get(
            f"/api/pull-requests/{created['id']}", headers=_auth_headers(author)
        )
        body = resp.json()
        assert body["author"] is None
        assert body["author_id"] is None
        assert body["is_own"] is True

    async def test_moderator_sees_author(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        author = await _create_user(db_session)
        moderator = await _create_user(db_session, UserRole.MODERATOR)
        await db_session.commit()

        pr = await _create_anonymous_pr(client, author)
        resp = await client.get(f"/api/pull-requests/{pr['id']}", headers=_auth_headers(moderator))

        body = resp.json()
        assert body["is_anonymous"] is True
        assert body["author_id"] == str(author.id)
        assert body["author"]["display_name"] == author.display_name

    async def test_author_filter_does_not_attribute_anonymous_prs(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        author = await _create_user(db_session)
        other = await _create_user(db_session)
        moderator = await _create_user(db_session, UserRole.MODERATOR)
        await db_session.commit()

        pr = await _create_anonymous_pr(client, author)
        query = f"/api/pull-requests?author_id={author.id}"

        as_other = await client.get(query, headers=_auth_headers(other))
        assert [p["id"] for p in as_other.json()] == []
        assert as_other.headers.get("x-total-count") in (None, "0")

        as_author = await client.get(query, headers=_auth_headers(author))
        assert [p["id"] for p in as_author.json()] == [pr["id"]]

        as_moderator = await client.get(query, headers=_auth_headers(moderator))
        assert [p["id"] for p in as_moderator.json()] == [pr["id"]]

    async def test_profile_hides_anonymous_prs_from_others(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        author = await _create_user(db_session)
        other = await _create_user(db_session)
        await db_session.commit()

        await _create_anonymous_pr(client, author)

        contributions = await client.get(
            f"/api/users/{author.id}/contributions?type=prs", headers=_auth_headers(other)
        )
        assert contributions.json()["items"] == []

        profile = await client.get(f"/api/users/{author.id}", headers=_auth_headers(other))
        assert profile.json()["prs_total"] == 0
        assert profile.json()["open_pr_count"] == 0

        own = await client.get(
            f"/api/users/{author.id}/contributions?type=prs", headers=_auth_headers(author)
        )
        items = own.json()["items"]
        assert len(items) == 1
        assert items[0]["is_anonymous"] is True
        assert items[0]["author"] is None


class TestAnonymousMaterialSerialization:
    def _material(self, **overrides: typing.Any) -> dict[str, typing.Any]:
        now = datetime.now(UTC)
        data: dict[str, typing.Any] = {
            "id": uuid.uuid4(),
            "directory_id": None,
            "title": "T",
            "slug": "t",
            "description": None,
            "type": "document",
            "current_version": 1,
            "parent_material_id": None,
            "author_id": uuid.uuid4(),
            "metadata": {},
            "download_count": 0,
            "total_views": 0,
            "views_today": 0,
            "like_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        data.update(overrides)
        return data

    def test_material_author_hidden_when_anonymous(self) -> None:
        assert MaterialOut.model_validate(self._material(is_anonymous=True)).author_id is None
        assert MaterialOut.model_validate(self._material()).author_id is not None

    def test_version_author_hidden_when_anonymous(self) -> None:
        version = MaterialVersionOut.model_validate(
            {
                "id": uuid.uuid4(),
                "material_id": uuid.uuid4(),
                "version_number": 1,
                "file_key": "cas/abc",
                "file_name": "a.pdf",
                "file_size": 1,
                "file_mime_type": "application/pdf",
                "diff_summary": None,
                "author_id": uuid.uuid4(),
                "is_anonymous": True,
                "pr_id": None,
                "virus_scan_result": "clean",
                "version_lock": 0,
                "created_at": datetime.now(UTC),
            }
        )
        assert version.author_id is None


def test_storage_keys_drop_uploader_id() -> None:
    uid = uuid.uuid4()
    upload_id = uuid.uuid4()
    key = f"uploads/{uid}/{upload_id}/notes.md"

    assert redact_user_scoped_key(key) == f"uploads/anonymous/{upload_id}/notes.md"
    assert anonymous_material_key(key) == f"materials/anonymous/{upload_id}/notes.md"
    assert redact_user_scoped_key("cas/abcdef") == "cas/abcdef"
