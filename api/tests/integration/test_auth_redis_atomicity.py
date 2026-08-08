from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response
from redis.asyncio import Redis
from starlette.requests import Request

from app.core.common.exceptions import UnauthorizedError
from app.core.security.security import create_refresh_token
from app.models.user import User, UserRole
from app.routers.auth import refresh_token
from app.services import auth as auth_service

pytestmark = pytest.mark.integration

_REDIS_URL = os.environ.get("AUTH_ATOMICITY_REDIS_URL")


@pytest.fixture
async def redis() -> Redis:  # type: ignore[type-arg]
    if not _REDIS_URL:
        pytest.skip("AUTH_ATOMICITY_REDIS_URL is required for this integration test")
    client = Redis.from_url(_REDIS_URL, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


async def test_refresh_jti_has_exactly_one_atomic_consumer(redis: Redis) -> None:  # type: ignore[type-arg]
    async def consume() -> bool:
        return await auth_service.consume_token_once(redis, "refresh-jti", 300)

    winners = await asyncio.gather(*(consume() for _ in range(32)))
    assert winners.count(True) == 1
    assert winners.count(False) == 31
    assert await auth_service.is_token_blacklisted(redis, "refresh-jti")


async def test_verification_code_has_exactly_one_concurrent_redeemer(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "atomic-code@example.com"
    code = "A2B3C4D5"
    await auth_service.store_code(redis, email, code)
    await auth_service.store_magic_token(redis, email, "paired-magic")

    winners = await asyncio.gather(
        *(auth_service.verify_code(redis, email, code) for _ in range(32))
    )
    assert winners.count(True) == 1
    assert winners.count(False) == 31
    assert await redis.get(f"auth:code:{email}") is None
    assert await redis.get("auth:magic:paired-magic") is None


async def test_magic_link_has_exactly_one_concurrent_redeemer(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "atomic-magic@example.com"
    token = "single-use-magic-token"
    await auth_service.store_code(redis, email, "H2J3K4M5")
    await auth_service.store_magic_token(redis, email, token)

    results = await asyncio.gather(
        *(auth_service.verify_magic_token(redis, token) for _ in range(32))
    )
    assert results.count(email) == 1
    assert results.count(None) == 31
    assert await redis.get(f"auth:magic:{token}") is None


async def test_code_and_magic_link_are_one_shared_single_use_challenge(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "atomic-shared@example.com"
    code = "N2P3Q4R5"
    token = "shared-magic-token"
    await auth_service.store_code(redis, email, code)
    await auth_service.store_magic_token(redis, email, token)

    code_result, magic_result = await asyncio.gather(
        auth_service.verify_code(redis, email, code),
        auth_service.verify_magic_token(redis, token),
    )
    assert int(code_result is True) + int(magic_result == email) == 1


async def test_session_family_revocation_round_trip(redis: Redis) -> None:  # type: ignore[type-arg]
    assert not await auth_service.is_session_revoked(redis, "session-1")
    await auth_service.revoke_session(redis, "session-1", 300)
    assert await auth_service.is_session_revoked(redis, "session-1")


def _refresh_request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/api/auth/refresh",
            "query_string": b"",
            "headers": [
                (b"cookie", f"refresh_token={token}".encode()),
                (b"x-client-id", b"integration"),
            ],
            "client": ("127.0.0.1", 443),
            "server": ("test", 443),
        }
    )


async def test_refresh_route_has_exactly_one_concurrent_winner(redis: Redis) -> None:  # type: ignore[type-arg]
    user = User(
        id=uuid.uuid4(),
        email="refresh-race@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    family = "refresh-family"
    token = create_refresh_token(str(user.id), expire_days=1, session_id=family)

    async def rotate():
        return await refresh_token(
            _refresh_request(token),
            Response(),
            AsyncMock(),
            redis,
        )

    with patch("app.routers.auth.get_user_by_id", AsyncMock(return_value=user)):
        results = await asyncio.gather(rotate(), rotate(), return_exceptions=True)

    successes = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], UnauthorizedError)
    # A detected sibling replay revokes the complete family so neither side can
    # retain a durable child session. The user must authenticate again.
    assert await auth_service.is_session_revoked(redis, family)
