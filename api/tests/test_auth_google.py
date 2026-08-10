from contextlib import contextmanager
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_config import AllowedDomain
from app.models.user import User, UserRole


@contextmanager
def _enable_google_oauth(allow_all: bool = True):
    from app.config import settings

    with (
        patch.object(settings, "google_oauth_enabled", True),
        patch.object(settings, "allow_all_domains", allow_all),
    ):
        yield


@pytest.fixture
def mock_google_verify():
    with patch("app.routers.auth.id_token.verify_oauth2_token") as mock_verify:
        yield mock_verify


@pytest.mark.asyncio
async def test_google_login_disabled(
    client: AsyncClient,
    db_session: AsyncSession,
):
    # Default settings.google_oauth_enabled = False
    response = await client.post(
        "/api/auth/google",
        json={"credential": "fake_token"},
    )
    assert response.status_code == 401
    assert "disabled" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_google_login_invalid_token(
    client: AsyncClient,
    mock_google_verify,
):
    mock_google_verify.side_effect = ValueError("Invalid token")

    with _enable_google_oauth():
        response = await client.post(
            "/api/auth/google",
            json={"credential": "invalid_token"},
        )
    assert response.status_code == 401
    assert "Invalid Google credential" in response.json()["detail"]


@pytest.mark.asyncio
async def test_google_login_invalid_issuer(
    client: AsyncClient,
    mock_google_verify,
):
    mock_google_verify.return_value = {
        "iss": "invalid_issuer",
        "email": "test@example.com",
    }

    with _enable_google_oauth():
        response = await client.post(
            "/api/auth/google",
            json={"credential": "fake_token"},
        )
    assert response.status_code == 401
    assert "Invalid Google issuer" in response.json()["detail"]


@pytest.mark.asyncio
async def test_google_login_success_new_user(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_google_verify,
):
    mock_google_verify.return_value = {
        "iss": "accounts.google.com",
        "email": "new.user@example.com",
        "email_verified": True,
        "given_name": "New",
        "family_name": "User",
        "picture": "https://lh3.googleusercontent.com/a/new-user=s96-c",
    }

    with _enable_google_oauth():
        response = await client.post(
            "/api/auth/google",
            json={"credential": "valid_token"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["access_token"] is not None
    assert data["is_new_user"] is True
    assert data["user"]["email"] == "new.user@example.com"
    assert data["user"]["display_name"] == "New User"
    assert data["user"]["avatar_url"] == "https://lh3.googleusercontent.com/a/new-user=s96-c"

    # Check Set-Cookie headers for refresh_token
    assert "refresh_token" in response.cookies


@pytest.mark.asyncio
async def test_google_login_success_existing_user(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_google_verify,
):
    # Create an existing user
    user = User(email="existing@example.com", display_name="Old Name", role=UserRole.STUDENT)
    db_session.add(user)
    await db_session.commit()

    mock_google_verify.return_value = {
        "iss": "https://accounts.google.com",
        "email": "existing@example.com",
        "email_verified": True,
        "given_name": "Overwritten",
        "family_name": "Name",
        "picture": "https://lh3.googleusercontent.com/a/existing-user=s96-c",
    }

    with _enable_google_oauth():
        response = await client.post(
            "/api/auth/google",
            json={"credential": "valid_token"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["is_new_user"] is False
    assert data["user"]["email"] == "existing@example.com"
    # Should not overwrite existing display_name
    assert data["user"]["display_name"] == "Old Name"
    # Should add avatar_url since it was empty
    assert data["user"]["avatar_url"] == "https://lh3.googleusercontent.com/a/existing-user=s96-c"


@pytest.mark.asyncio
async def test_google_login_domain_restriction(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_google_verify,
):
    # Config with allow_all_domains=False and a specific allowed domain
    domain = AllowedDomain(domain="allowed.com", auto_approve=True)
    db_session.add(domain)
    await db_session.commit()

    mock_google_verify.return_value = {
        "iss": "accounts.google.com",
        "email": "hacker@evil.com",
        "email_verified": True,
        "given_name": "Evil",
    }

    with _enable_google_oauth(allow_all=False):
        response = await client.post(
            "/api/auth/google",
            json={"credential": "valid_token"},
        )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_google_login_does_not_persist_untrusted_picture_url(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_google_verify,
):
    mock_google_verify.return_value = {
        "iss": "accounts.google.com",
        "email": "untrusted-picture@example.com",
        "email_verified": True,
        "given_name": "No",
        "family_name": "Redirect",
        "picture": "https://attacker.example/profile.png",
    }

    with _enable_google_oauth():
        response = await client.post(
            "/api/auth/google",
            json={"credential": "valid_token"},
        )

    assert response.status_code == 200
    assert response.json()["user"]["avatar_url"] is None


@pytest.mark.asyncio
async def test_google_login_replaces_unsafe_legacy_avatar_with_trusted_picture(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_google_verify,
):
    user = User(
        email="legacy-avatar@example.com",
        display_name="Legacy",
        role=UserRole.STUDENT,
        avatar_url="cas/victim-staged-object",
    )
    db_session.add(user)
    await db_session.commit()

    trusted_picture = "https://lh3.googleusercontent.com/a/healed=s96-c"
    mock_google_verify.return_value = {
        "iss": "accounts.google.com",
        "email": user.email,
        "email_verified": True,
        "picture": trusted_picture,
    }

    with _enable_google_oauth():
        response = await client.post(
            "/api/auth/google",
            json={"credential": "valid_token"},
        )

    assert response.status_code == 200
    assert response.json()["user"]["avatar_url"] == trusted_picture
