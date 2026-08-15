import uuid

import pytest
from fastapi import Response
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.routers.auth import _login_response


async def _create_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_me_returns_completed_tutorials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.get("/api/users/me", headers=_auth_headers(user))
    assert response.status_code == 200
    assert response.json()["completed_tutorials"] == []


@pytest.mark.asyncio
async def test_auth_response_preserves_completed_tutorials(
    db_session: AsyncSession,
) -> None:
    """Login/bootstrap responses must preserve tutorial completion state."""
    user = await _create_user(db_session)
    user.completed_tutorials = ["welcome", "browse"]
    await db_session.flush()

    auth_response = _login_response(user, Response(), is_new=False)

    assert auth_response.user.completed_tutorials == ["welcome", "browse"]


@pytest.mark.asyncio
async def test_complete_tutorial_is_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    headers = _auth_headers(user)

    for _ in range(2):
        response = await client.post(
            "/api/users/me/tutorials/complete",
            json={"tutorial_id": "welcome"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["completed_tutorials"] == ["welcome"]

    response = await client.post(
        "/api/users/me/tutorials/complete",
        json={"tutorial_id": "browse"},
        headers=headers,
    )
    assert response.json()["completed_tutorials"] == ["welcome", "browse"]


@pytest.mark.asyncio
async def test_reset_tutorials(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    headers = _auth_headers(user)

    await client.post(
        "/api/users/me/tutorials/complete",
        json={"tutorial_id": "welcome"},
        headers=headers,
    )
    response = await client.delete("/api/users/me/tutorials", headers=headers)
    assert response.status_code == 200
    assert response.json()["completed_tutorials"] == []


@pytest.mark.asyncio
async def test_complete_tutorial_rejects_invalid_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/api/users/me/tutorials/complete",
        json={"tutorial_id": "Not Valid!"},
        headers=_auth_headers(user),
    )
    assert response.status_code == 422
