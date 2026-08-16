"""Regression tests for authentication enforcement across docs, openapi, comments, browse, and users endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.security import create_access_token, create_browser_read_token
from app.dependencies.auth import BROWSER_READ_COOKIE
from app.models.directory import Directory
from app.models.material import Material
from app.models.user import User, UserRole


async def _create_test_user(
    db: AsyncSession,
    *,
    role: UserRole = UserRole.STUDENT,
    onboarded: bool = True,
    display_name: str = "Test User",
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name=display_name,
        role=role,
        onboarded=onboarded,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _create_test_directory(db: AsyncSession, user: User, *, name: str = "Test Folder") -> Directory:
    unique_suffix = uuid.uuid4().hex[:8]
    directory = Directory(
        id=uuid.uuid4(),
        name=f"{name} {unique_suffix}",
        slug=f"{name.lower().replace(' ', '-')}-{unique_suffix}",
        type="folder",
        created_by=user.id,
    )
    db.add(directory)
    await db.flush()
    return directory


def _auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


def _browser_read_cookies(user: User) -> dict[str, str]:
    return {BROWSER_READ_COOKIE: create_browser_read_token(str(user.id))}


# ── /api/docs and /api/openapi.json ──────────────────────────────────────────


async def test_docs_unauthenticated_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/docs")
    assert response.status_code == 401


async def test_docs_authenticated_bearer_returns_html(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    await db_session.commit()

    response = await client.get("/api/docs", headers=_auth_headers(user))
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "swagger-ui" in response.text


async def test_docs_authenticated_cookie_returns_html(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    await db_session.commit()

    response = await client.get("/api/docs", cookies=_browser_read_cookies(user))
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "swagger-ui" in response.text


async def test_openapi_unauthenticated_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/openapi.json")
    assert response.status_code == 401


async def test_openapi_authenticated_bearer_returns_schema(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    await db_session.commit()

    response = await client.get("/api/openapi.json", headers=_auth_headers(user))
    assert response.status_code == 200
    schema = response.json()
    assert "openapi" in schema
    assert "paths" in schema
    assert "info" in schema


async def test_openapi_authenticated_cookie_returns_schema(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    await db_session.commit()

    response = await client.get("/api/openapi.json", cookies=_browser_read_cookies(user))
    assert response.status_code == 200
    schema = response.json()
    assert "openapi" in schema
    assert "paths" in schema


# ── /api/comments ────────────────────────────────────────────────────────────


async def test_comments_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user)
    await db_session.commit()

    response = await client.get(
        f"/api/comments?targetType=directory&targetId={directory.id}"
    )
    assert response.status_code == 401


async def test_comments_authenticated_returns_paginated_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user)
    await db_session.commit()

    response = await client.get(
        f"/api/comments?targetType=directory&targetId={directory.id}",
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert data["total"] == 0


# ── /api/browse & /api/directories ───────────────────────────────────────────


async def test_browse_root_unauthenticated_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/browse")
    assert response.status_code == 401


async def test_browse_root_authenticated_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    await db_session.commit()

    response = await client.get("/api/browse", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "directory_listing"


async def test_browse_path_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user, name="Course")
    await db_session.commit()

    response = await client.get(f"/api/browse/{directory.slug}")
    assert response.status_code == 401


async def test_browse_path_authenticated_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user, name="Course")
    await db_session.commit()

    response = await client.get(
        f"/api/browse/{directory.slug}", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "directory_listing"
    assert data["directory"]["name"] == directory.name


async def test_directory_by_id_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user)
    await db_session.commit()

    response = await client.get(f"/api/directories/{directory.id}")
    assert response.status_code == 401


async def test_directory_by_id_authenticated_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user)
    await db_session.commit()

    response = await client.get(
        f"/api/directories/{directory.id}", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(directory.id)


async def test_directory_children_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user)
    await db_session.commit()

    response = await client.get(f"/api/directories/{directory.id}/children")
    assert response.status_code == 401


async def test_directory_children_authenticated_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user)
    await db_session.commit()

    response = await client.get(
        f"/api/directories/{directory.id}/children", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    assert "directories" in response.json()


async def test_directory_path_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user)
    await db_session.commit()

    response = await client.get(f"/api/directories/{directory.id}/path")
    assert response.status_code == 401


async def test_directory_path_authenticated_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    directory = await _create_test_directory(db_session, user)
    await db_session.commit()

    response = await client.get(
        f"/api/directories/{directory.id}/path", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


# ── /api/users ───────────────────────────────────────────────────────────────


async def test_user_profile_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    await db_session.commit()

    response = await client.get(f"/api/users/{user.id}")
    assert response.status_code == 401


async def test_user_profile_authenticated_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session, display_name="Profile User")
    await db_session.commit()

    response = await client.get(f"/api/users/{user.id}", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Profile User"


async def test_user_avatar_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    user.avatar_url = f"avatars/{user.id}/{uuid.uuid4()}.webp"
    await db_session.commit()

    response = await client.get(f"/api/users/{user.id}/avatar")
    assert response.status_code == 401


async def test_user_avatar_authenticated_bearer_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    avatar_key = f"avatars/{user.id}/{uuid.uuid4()}.webp"
    user.avatar_url = avatar_key
    await db_session.commit()

    with patch(
        "app.routers.users.generate_presigned_get",
        new_callable=AsyncMock,
        return_value="https://storage.example/avatar.webp",
    ):
        response = await client.get(
            f"/api/users/{user.id}/avatar",
            headers=_auth_headers(user),
            follow_redirects=False,
        )
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "https://storage.example/avatar.webp"


async def test_user_avatar_authenticated_cookie_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    avatar_key = f"avatars/{user.id}/{uuid.uuid4()}.webp"
    user.avatar_url = avatar_key
    await db_session.commit()

    with patch(
        "app.routers.users.generate_presigned_get",
        new_callable=AsyncMock,
        return_value="https://storage.example/avatar.webp",
    ):
        response = await client.get(
            f"/api/users/{user.id}/avatar",
            cookies=_browser_read_cookies(user),
            follow_redirects=False,
        )
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "https://storage.example/avatar.webp"


async def test_user_contributions_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    await db_session.commit()

    response = await client.get(f"/api/users/{user.id}/contributions?type=prs")
    assert response.status_code == 401


async def test_user_contributions_authenticated_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_test_user(db_session)
    await db_session.commit()

    response = await client.get(
        f"/api/users/{user.id}/contributions?type=prs",
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
