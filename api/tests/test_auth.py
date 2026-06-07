from unittest.mock import AsyncMock

from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert "details" in data


async def test_request_code_invalid_domain(client: AsyncClient) -> None:
    # Domain validation is now async (DB-backed); invalid domains return 400 not 422
    response = await client.post(
        "/api/auth/request-code",
        json={"email": "test@gmail.com"},
    )
    assert response.status_code == 400


async def test_request_code_plus_alias(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/request-code",
        json={"email": "test+alias@telecom-sudparis.eu"},
    )
    assert response.status_code == 422


async def test_verify_code_invalid(client: AsyncClient, mock_redis: AsyncMock) -> None:
    from app.config import settings

    original_env = settings.environment
    settings.environment = "production"

    # Mock redis to return no previous attempts
    mock_redis.get = AsyncMock(return_value=None)

    try:
        response = await client.post(
            "/api/auth/verify-code",
            json={"email": "test@telecom-sudparis.eu", "code": "WRONGCOD"},
        )
        assert response.status_code == 400
        # Check that increment was called
        mock_redis.pipeline.assert_called()
    finally:
        settings.environment = original_env


async def test_verify_code_rate_limit(client: AsyncClient, mock_redis: AsyncMock) -> None:
    from app.config import settings
    from app.services import auth as auth_service

    email = "test@telecom-sudparis.eu"

    # Force production environment for rate limit check
    original_env = settings.environment
    settings.environment = "production"

    try:
        # Mock redis to return max rate limit
        mock_redis.get = AsyncMock(return_value=str(auth_service.VERIFY_RATE_LIMIT_MAX))

        response = await client.post(
            "/api/auth/verify-code",
            json={"email": email, "code": "A2B3C4D5"},
        )
        assert response.status_code == 429
        assert "Too many verification attempts" in response.json()["detail"]
    finally:
        settings.environment = original_env


async def test_setup_creates_first_admin(client: AsyncClient) -> None:
    # Fresh instance: no admin exists yet, so setup is allowed.
    methods = await client.get("/api/auth/methods")
    assert methods.status_code == 200
    assert methods.json()["needs_setup"] is True

    response = await client.post(
        "/api/auth/setup",
        json={
            "email": "admin@telecom-sudparis.eu",
            "password": "supersecret123",
            "display_name": "First Admin",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["role"] == "bureau"
    assert data["user"]["onboarded"] is True
    assert data["access_token"]

    # Once an admin exists, the instance no longer advertises setup.
    methods = await client.get("/api/auth/methods")
    assert methods.json()["needs_setup"] is False


async def test_setup_blocked_when_admin_exists(client: AsyncClient) -> None:
    first = await client.post(
        "/api/auth/setup",
        json={"email": "admin@telecom-sudparis.eu", "password": "supersecret123"},
    )
    assert first.status_code == 200

    # Second attempt must be permanently rejected — this guards privilege escalation.
    second = await client.post(
        "/api/auth/setup",
        json={"email": "intruder@telecom-sudparis.eu", "password": "supersecret123"},
    )
    assert second.status_code == 409


async def test_setup_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/setup",
        json={"email": "admin@telecom-sudparis.eu", "password": "short"},
    )
    assert response.status_code == 422
