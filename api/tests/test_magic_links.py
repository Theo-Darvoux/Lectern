from typing import Any
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_config import AllowedDomain


async def test_magic_link_flow_success(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup: Any
) -> None:
    # 1. Request code (which generates magic token)
    email = "test@telecom-sudparis.eu"

    db_session.add(AllowedDomain(domain="telecom-sudparis.eu", auto_approve=True))
    await db_session.flush()

    with patch("app.routers.auth.send_verification_email", new_callable=AsyncMock) as mock_send:
        response = await client.post(
            "/api/auth/request-code",
            json={"email": email},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Verification code sent"}

        # Check if email was "sent" (mocked)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        sent_email, _, sent_magic_link = args[0], args[1], args[2]
        assert sent_email == email
        assert "/login/verify?token=" in sent_magic_link

        magic_token = sent_magic_link.split("token=")[1]

    # 2. Verify magic link
    response = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": magic_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["user"]["email"] == email
    assert data["user"]["role"] == "student"

    # Check that refresh token cookie is set
    assert "refresh_token" in response.cookies

    # 3. Verify token is single-use
    response = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": magic_token},
    )
    assert response.status_code == 400
    assert "Invalid or expired magic link" in response.json()["detail"]


async def test_magic_link_new_user_signup(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup: Any
) -> None:
    email = "newuser@telecom-sudparis.eu"
    db_session.add(AllowedDomain(domain="telecom-sudparis.eu", auto_approve=True))
    await db_session.flush()

    # Step 1: Request code
    with patch("app.routers.auth.send_verification_email", new_callable=AsyncMock) as mock_send:
        response = await client.post("/api/auth/request-code", json={"email": email})
        assert response.status_code == 200
        sent_magic_link = mock_send.call_args[0][2]
        magic_token = sent_magic_link.split("token=")[1]

    # Step 2: Verify magic link
    response = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": magic_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_new_user"] is True
    assert data["user"]["onboarded"] is False


async def test_magic_link_invalid_token(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": "invalid_token_123"},
    )
    assert response.status_code == 400
    assert "Invalid or expired magic link" in response.json()["detail"]


async def test_magic_link_pending_approval(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup: Any
) -> None:
    # Domain allowed but NOT auto-approve
    email = "pending@manual.edu"
    db_session.add(AllowedDomain(domain="manual.edu", auto_approve=False))
    await db_session.flush()

    # Step 1: Request code
    with patch("app.routers.auth.send_verification_email", new_callable=AsyncMock) as mock_send:
        response = await client.post("/api/auth/request-code", json={"email": email})
        assert response.status_code == 200
        sent_magic_link = mock_send.call_args[0][2]
        magic_token = sent_magic_link.split("token=")[1]

    # Step 2: Verify magic link
    response = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": magic_token},
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "pending"


async def test_magic_link_expired(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup: Any
) -> None:
    email = "expired@telecom-sudparis.eu"
    db_session.add(AllowedDomain(domain="telecom-sudparis.eu", auto_approve=True))
    await db_session.flush()

    # Request code
    with patch("app.routers.auth.send_verification_email", new_callable=AsyncMock) as mock_send:
        response = await client.post("/api/auth/request-code", json={"email": email})
        assert response.status_code == 200
        sent_magic_link = mock_send.call_args[0][2]
        magic_token = sent_magic_link.split("token=")[1]

    # Manually expire from fake redis
    await fake_redis_setup.delete(f"auth:magic:{magic_token}")

    # Try to verify
    response = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": magic_token},
    )
    assert response.status_code == 400
