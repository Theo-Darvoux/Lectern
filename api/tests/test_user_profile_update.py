import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


async def _create_user(
    db: AsyncSession,
    *,
    role: UserRole = UserRole.STUDENT,
    onboarded: bool = True,
    gdpr_consent: bool = True,
    display_name: str = "Tester",
    bio: str | None = None,
    avatar_url: str | None = None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name=display_name,
        role=role,
        onboarded=onboarded,
        gdpr_consent=gdpr_consent,
        bio=bio,
        avatar_url=avatar_url,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_update_profile_bio(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session, bio="Original bio")
    await db_session.commit()

    # 1. Update bio to new value
    response = await client.patch(
        "/api/users/me",
        json={"bio": "New bio"},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    assert response.json()["bio"] == "New bio"

    # 2. Clear bio by sending empty string
    response = await client.patch(
        "/api/users/me",
        json={"bio": ""},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    assert response.json()["bio"] == ""

    # 3. Clear bio by sending null
    response = await client.patch(
        "/api/users/me",
        json={"bio": None},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    assert response.json()["bio"] is None


@pytest.mark.asyncio
async def test_update_profile_omitted_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session, bio="Don't change me", display_name="Keeper")
    await db_session.commit()

    # Update only academic_year, others should remain unchanged
    response = await client.patch(
        "/api/users/me",
        json={"academic_year": "2A"},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["academic_year"] == "2A"
    assert data["bio"] == "Don't change me"
    assert data["display_name"] == "Keeper"


@pytest.mark.asyncio
async def test_clear_avatar(client: AsyncClient, db_session: AsyncSession, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import app.services.user as user_service

    monkeypatch.setattr(user_service, "delete_object", AsyncMock())

    user = await _create_user(db_session, avatar_url="avatars/test.webp")
    await db_session.commit()

    # Mock storage cleanup if needed, but here we just check if DB is updated
    response = await client.patch(
        "/api/users/me",
        json={"avatar_url": None},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    assert response.json()["avatar_url"] is None

    # Verify in DB
    await db_session.refresh(user)
    assert user.avatar_url is None
