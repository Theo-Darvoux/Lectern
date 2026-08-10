from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi import Response
from redis.asyncio import Redis
from starlette.requests import Request

from app.config import settings
from app.core.common.exceptions import UnauthorizedError
from app.core.security.cas import (
    CasReferenceError,
    CasReferenceMissingError,
    _operation_marker_key,
    compensate_cas_increment,
    hmac_cas_key,
    increment_cas_ref,
)
from app.core.security.security import ALGORITHM, create_refresh_token
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


@pytest.mark.parametrize(
    ("consumer", "maximum"),
    [
        (auth_service.check_rate_limit, auth_service.RATE_LIMIT_MAX),
        (auth_service.check_verify_rate_limit, auth_service.VERIFY_RATE_LIMIT_MAX),
    ],
)
async def test_auth_rate_limits_have_exactly_the_configured_concurrent_winners(
    redis: Redis,  # type: ignore[type-arg]
    consumer: object,
    maximum: int,
) -> None:
    async def consume() -> bool:
        return await consumer(redis, "concurrent-limit@example.invalid")  # type: ignore[operator]

    with patch.object(settings, "environment", "production"):
        winners = await asyncio.gather(*(consume() for _ in range(32)))

    assert winners.count(True) == maximum
    assert winners.count(False) == 32 - maximum


async def test_verification_code_has_exactly_one_concurrent_redeemer(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "atomic-code@example.com"
    code = "A2B3C4D5"
    token = "paired-magic"
    await auth_service.store_login_challenge(redis, email, code, token, auth_generation=7)
    assert await redis.get(f"auth:challenge_gen:{email}") == "7"

    winners = await asyncio.gather(
        *(auth_service.verify_code(redis, email, code) for _ in range(32))
    )
    assert winners.count(True) == 1
    assert winners.count(False) == 31
    assert await redis.get(f"auth:code:{email}") is None
    assert await redis.get(f"auth:magic:{token}") is None
    assert await redis.get(f"auth:challenge_gen:{email}") is None


async def test_magic_link_has_exactly_one_concurrent_redeemer(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "atomic-magic@example.com"
    token = "single-use-magic-token"
    await auth_service.store_login_challenge(redis, email, "H2J3K4M5", token, auth_generation=11)
    assert await redis.get(f"auth:challenge_gen:{email}") == "11"

    results = await asyncio.gather(
        *(auth_service.verify_magic_token(redis, token) for _ in range(32))
    )
    assert results.count(email) == 1
    assert results.count(None) == 31
    assert await redis.get(f"auth:magic:{token}") is None
    assert await redis.get(f"auth:challenge_gen:{email}") is None


async def test_login_challenge_consumers_return_the_issuance_generation(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    code_email = "generation-code@example.com"
    await auth_service.store_login_challenge(
        redis, code_email, "A2B3C4D5", "generation-code-magic", auth_generation=23
    )
    assert await auth_service.consume_verification_code(redis, code_email, "A2B3C4D5") == 23

    magic_email = "generation-magic@example.com"
    magic_token = "generation-magic-token"
    await auth_service.store_login_challenge(
        redis, magic_email, "H2J3K4M5", magic_token, auth_generation=29
    )
    assert await auth_service.consume_magic_token(redis, magic_token) == (magic_email, 29)


async def test_legacy_login_challenge_without_generation_decodes_as_zero(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "legacy-generation@example.com"
    token = "legacy-generation-magic"
    await redis.setex(f"auth:code:{email}", auth_service.CODE_TTL_SECONDS, "N2P3Q4R5")
    await redis.setex(f"auth:magic:{token}", auth_service.CODE_TTL_SECONDS, email)
    await redis.setex(f"auth:magic_ref:{email}", auth_service.CODE_TTL_SECONDS, token)

    assert await auth_service.consume_magic_token(redis, token) == (email, 0)


async def test_code_and_magic_link_are_one_shared_single_use_challenge(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "atomic-shared@example.com"
    code = "N2P3Q4R5"
    token = "shared-magic-token"
    await auth_service.store_login_challenge(redis, email, code, token, auth_generation=0)

    code_result, magic_result = await asyncio.gather(
        auth_service.verify_code(redis, email, code),
        auth_service.verify_magic_token(redis, token),
    )
    assert int(code_result is True) + int(magic_result == email) == 1


async def test_new_challenge_invalidates_previous_magic_link(redis: Redis) -> None:  # type: ignore[type-arg]
    email = "supersede@example.com"
    await auth_service.store_login_challenge(redis, email, "A2B3C4D5", "magic-a", auth_generation=0)
    await auth_service.store_login_challenge(redis, email, "H2J3K4M5", "magic-b", auth_generation=0)

    assert await auth_service.verify_magic_token(redis, "magic-a") is None
    assert await auth_service.verify_magic_token(redis, "magic-b") == email


async def test_concurrent_challenge_issuance_leaves_one_coherent_generation(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "issuance-race@example.com"
    await asyncio.gather(
        auth_service.store_login_challenge(redis, email, "A2B3C4D5", "magic-a", auth_generation=3),
        auth_service.store_login_challenge(redis, email, "H2J3K4M5", "magic-b", auth_generation=4),
    )

    current_magic = await redis.get(f"auth:magic_ref:{email}")
    current_code = await redis.get(f"auth:code:{email}")
    current_generation = await redis.get(f"auth:challenge_gen:{email}")
    assert current_magic in {"magic-a", "magic-b"}

    if current_magic == "magic-a":
        assert current_code == "A2B3C4D5"
        assert current_generation == "3"
        assert await redis.get("auth:magic:magic-a") == email
        assert await redis.get("auth:magic:magic-b") is None
    else:
        assert current_code == "H2J3K4M5"
        assert current_generation == "4"
        assert await redis.get("auth:magic:magic-b") == email
        assert await redis.get("auth:magic:magic-a") is None


async def test_redeeming_current_code_invalidates_all_magic_generations(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    email = "code-supersession@example.com"
    await auth_service.store_login_challenge(redis, email, "A2B3C4D5", "magic-a", auth_generation=0)
    await auth_service.store_login_challenge(redis, email, "H2J3K4M5", "magic-b", auth_generation=0)

    assert await auth_service.verify_code(redis, email, "H2J3K4M5")
    assert await auth_service.verify_magic_token(redis, "magic-a") is None
    assert await auth_service.verify_magic_token(redis, "magic-b") is None


async def test_session_family_revocation_round_trip(redis: Redis) -> None:  # type: ignore[type-arg]
    assert not await auth_service.is_session_revoked(redis, "session-1")
    await auth_service.revoke_session(redis, "session-1", 300)
    assert await auth_service.is_session_revoked(redis, "session-1")


async def test_cas_compensation_does_not_decrement_an_unattempted_increment(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    sha256 = "a" * 64
    await increment_cas_ref(
        redis,
        sha256,
        initial_data={"file_key": "cas/shared", "size": 7},
        operation_id="existing-owner",
    )

    assert await compensate_cas_increment(redis, sha256, operation_id="failed-before-send") == 1
    raw = await redis.get(hmac_cas_key(sha256))
    assert raw is not None
    assert '"ref_count":1' in raw
    with pytest.raises(CasReferenceError, match="already compensated"):
        await increment_cas_ref(redis, sha256, operation_id="failed-before-send")


async def test_cas_compensation_recovers_a_lost_success_exactly_once(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    sha256 = "b" * 64
    await increment_cas_ref(
        redis,
        sha256,
        initial_data={"file_key": "cas/shared", "size": 7},
        operation_id="existing-owner",
    )
    await increment_cas_ref(redis, sha256, operation_id="ambiguous-increment")

    assert await compensate_cas_increment(redis, sha256, operation_id="ambiguous-increment") == 1
    assert await compensate_cas_increment(redis, sha256, operation_id="ambiguous-increment") == 1


async def test_cas_duplicate_marker_without_record_fails_closed(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    operation_id = "evicted-record"
    await redis.set(_operation_marker_key(operation_id), "incremented")

    with pytest.raises(CasReferenceMissingError):
        await increment_cas_ref(redis, "c" * 64, operation_id=operation_id)


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
        auto_approve=True,
        auth_generation=0,
    )
    family = "refresh-family"
    token = create_refresh_token(
        str(user.id),
        expire_days=1,
        session_id=family,
        auth_generation=user.auth_generation,
    )

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
    assert len(successes) == 1, [repr(result) for result in results]
    assert len(failures) == 1, [repr(result) for result in results]
    assert isinstance(failures[0], UnauthorizedError)
    assert await auth_service.is_session_revoked(redis, family)


async def test_legacy_refresh_without_session_family_requires_reauthentication(
    redis: Redis,  # type: ignore[type-arg]
) -> None:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "type": "refresh",
            "exp": 4102444800,
        },
        settings.secret_key.get_secret_value(),
        algorithm=ALGORITHM,
    )

    with pytest.raises(UnauthorizedError, match="Legacy refresh token requires reauthentication"):
        await refresh_token(
            _refresh_request(token),
            Response(),
            AsyncMock(),
            redis,
        )
