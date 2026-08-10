from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


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
        json={"email": "test+alias@example.com"},
    )
    assert response.status_code == 422


async def test_verify_code_invalid(client: AsyncClient, mock_redis: AsyncMock) -> None:
    from app.config import settings

    original_env = settings.environment
    settings.environment = "production"

    try:
        response = await client.post(
            "/api/auth/verify-code",
            json={"email": "test@example.com", "code": "WRONGCOD"},
        )
        assert response.status_code == 400
        # The attempt is consumed atomically before the verifier runs.
        mock_redis.pipeline.assert_called()
    finally:
        settings.environment = original_env


async def test_verify_code_rate_limit(client: AsyncClient, mock_redis: AsyncMock) -> None:
    from app.config import settings
    from app.services import auth as auth_service

    email = "test@example.com"

    # Force production environment for rate limit check
    original_env = settings.environment
    settings.environment = "production"

    try:
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [auth_service.VERIFY_RATE_LIMIT_MAX + 1, True]

        response = await client.post(
            "/api/auth/verify-code",
            json={"email": email, "code": "A2B3C4D5"},
        )
        assert response.status_code == 429
        assert "Too many verification attempts" in response.json()["detail"]
    finally:
        settings.environment = original_env


async def test_setup_creates_first_admin(client: AsyncClient) -> None:
    # Fresh non-production instance: durable installation marker is absent, so setup is allowed.
    methods = await client.get("/api/auth/methods")
    assert methods.status_code == 200
    assert methods.json()["needs_setup"] is True

    response = await client.post(
        "/api/auth/setup",
        json={
            "email": "admin@example.com",
            "password": "supersecret123",
            "display_name": "First Admin",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["role"] == "bureau"
    assert data["user"]["onboarded"] is True
    assert data["access_token"]

    # Once the installation marker is committed, setup is permanently consumed.
    methods = await client.get("/api/auth/methods")
    assert methods.json()["needs_setup"] is False


async def test_setup_blocked_when_admin_exists(client: AsyncClient) -> None:
    first = await client.post(
        "/api/auth/setup",
        json={"email": "admin@example.com", "password": "supersecret123"},
    )
    assert first.status_code == 200

    # Second attempt must be permanently rejected — this guards privilege escalation.
    second = await client.post(
        "/api/auth/setup",
        json={"email": "intruder@example.com", "password": "supersecret123"},
    )
    assert second.status_code == 409


async def test_setup_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/setup",
        json={"email": "admin@example.com", "password": "short"},
    )
    assert response.status_code == 422


async def test_production_setup_requires_configured_operator_token(client: AsyncClient) -> None:
    from app.config import settings

    original_env = settings.environment
    original_token = settings.bootstrap_token
    settings.environment = "production"
    settings.bootstrap_token = None
    try:
        methods = await client.get("/api/auth/methods")
        assert methods.status_code == 200
        assert methods.json()["needs_setup"] is True
        assert methods.json()["bootstrap_token_required"] is True

        response = await client.post(
            "/api/auth/setup",
            json={
                "email": "admin@example.com",
                "password": "supersecret123",
                "bootstrap_token": "a" * 64,
            },
        )
        assert response.status_code == 503
        assert response.json()["error_code"] == "BOOTSTRAP_TOKEN_NOT_CONFIGURED"
    finally:
        settings.environment = original_env
        settings.bootstrap_token = original_token


async def test_production_setup_token_is_one_time_and_not_reopened_by_admin_loss(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from pydantic import SecretStr
    from sqlalchemy import delete

    from app.config import settings
    from app.models.user import User

    original_env = settings.environment
    original_token = settings.bootstrap_token
    settings.environment = "production"
    settings.bootstrap_token = SecretStr("b" * 64)
    try:
        wrong = await client.post(
            "/api/auth/setup",
            json={
                "email": "admin@example.com",
                "password": "supersecret123",
                "bootstrap_token": "a" * 64,
            },
        )
        assert wrong.status_code == 401

        created = await client.post(
            "/api/auth/setup",
            json={
                "email": "admin@example.com",
                "password": "supersecret123",
                "bootstrap_token": "b" * 64,
            },
        )
        assert created.status_code == 200

        # Simulate catastrophic/operator-level removal outside normal protected APIs.
        # The irreversible installation marker must still prevent HTTP re-bootstrap.
        await db_session.execute(delete(User))
        await db_session.commit()

        methods = await client.get("/api/auth/methods")
        assert methods.json()["needs_setup"] is False
        replay = await client.post(
            "/api/auth/setup",
            json={
                "email": "intruder@example.com",
                "password": "supersecret123",
                "bootstrap_token": "b" * 64,
            },
        )
        assert replay.status_code == 409
    finally:
        settings.environment = original_env
        settings.bootstrap_token = original_token
