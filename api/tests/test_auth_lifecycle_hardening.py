from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.common.exceptions import UnauthorizedError
from app.core.security.security import (
    create_access_token,
    create_browser_read_token,
    create_refresh_token,
    decode_token,
)
from app.dependencies.auth import _validate_token_payload
from app.models.auth_config import AllowedDomain
from app.models.user import User, UserRole
from app.services import auth as auth_service


def test_all_token_creators_emit_session_family_by_default() -> None:
    access, _ = create_access_token("u", "student", "u@example.com")
    refresh = create_refresh_token("u")
    browser = create_browser_read_token("u")

    assert decode_token(access, expected_type="access")["sid"]
    assert decode_token(refresh, expected_type="refresh")["sid"]
    assert decode_token(browser, expected_type="browser_read")["sid"]


def test_issue_tokens_share_one_family_when_caller_omits_sid() -> None:
    user = User(
        id=uuid.uuid4(),
        email="family@example.com",
        role=UserRole.STUDENT,
        auto_approve=True,
    )
    access, refresh, _ = auth_service.issue_tokens(user)
    assert (
        decode_token(access, expected_type="access")["sid"]
        == decode_token(refresh, expected_type="refresh")["sid"]
    )


async def test_shared_auth_validator_rejects_sidless_legacy_credentials(
    db_session,
    fake_redis_setup,
) -> None:
    user = User(
        id=uuid.uuid4(),
        email="legacy@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
    )
    db_session.add(user)
    await db_session.flush()

    for token_type in ("access", "browser_read"):
        with pytest.raises(UnauthorizedError, match="Legacy session requires reauthentication"):
            await _validate_token_payload(
                {
                    "type": token_type,
                    "sub": str(user.id),
                    "jti": str(uuid.uuid4()),
                },
                db_session,
                fake_redis_setup,
                expected_type=token_type,
            )


async def test_offline_recovery_invalidates_all_pre_recovery_token_generations(
    db_session,
    fake_redis_setup,
    monkeypatch,
) -> None:
    user = User(
        id=uuid.uuid4(),
        email="recover-generation@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
    )
    db_session.add(user)
    await db_session.flush()

    session_id = str(uuid.uuid4())
    access, refresh, _ = auth_service.issue_tokens(user, session_id=session_id)
    browser = create_browser_read_token(
        str(user.id),
        session_id=session_id,
        auth_generation=user.auth_generation,
    )
    access_payload = decode_token(access, expected_type="access")
    refresh_payload = decode_token(refresh, expected_type="refresh")
    browser_payload = decode_token(browser, expected_type="browser_read")

    # Avoid coupling this invariant test to bcrypt runtime cost.
    monkeypatch.setattr(auth_service, "get_password_hash", lambda _password: "recovery-hash")
    recovered, created = await auth_service.recover_admin_account(
        db_session, user.email, "replacement-password"
    )

    assert created is False
    assert recovered.id == user.id
    assert recovered.role == UserRole.BUREAU
    assert recovered.auth_generation == 1
    assert auth_service.token_matches_auth_generation(access_payload, recovered) is False
    assert auth_service.token_matches_auth_generation(refresh_payload, recovered) is False
    assert auth_service.token_matches_auth_generation(browser_payload, recovered) is False

    for payload, token_type in (
        (access_payload, "access"),
        (browser_payload, "browser_read"),
    ):
        with pytest.raises(UnauthorizedError, match="reauthentication"):
            await _validate_token_payload(
                payload,
                db_session,
                fake_redis_setup,
                expected_type=token_type,
            )


def test_production_magic_link_paths_use_atomic_fragment_only_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    router = (root / "api/app/routers/auth.py").read_text(encoding="utf-8")
    cli = (root / "api/app/cli.py").read_text(encoding="utf-8")

    assert "store_login_challenge" in router
    assert "store_login_challenge" in cli
    assert "login_challenge_auth_generation" in router
    assert "login_challenge_auth_generation" in cli
    assert "/login/verify?token=" not in router
    assert "/login/verify?token=" not in cli
    assert "/login/verify#token=" in router
    assert "/login/verify#token=" in cli

    service_path = root / "api/app/services/auth.py"
    forbidden_callers = []
    for path in (root / "api/app").rglob("*.py"):
        if path == service_path:
            continue
        source = path.read_text(encoding="utf-8")
        if "store_code(" in source or "store_magic_token(" in source:
            forbidden_callers.append(str(path.relative_to(root)))
    assert forbidden_callers == []


async def test_offline_recovery_invalidates_pre_recovery_verification_code(
    client,
    db_session,
    fake_redis_setup,
    monkeypatch,
) -> None:
    email = "recover-code-boundary@example.com"
    user = User(
        id=uuid.uuid4(),
        email=email,
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
    )
    db_session.add_all([user, AllowedDomain(domain="example.com", auto_approve=True)])
    await db_session.flush()

    old_code = "A2B3C4D5"
    old_magic = "pre-recovery-code-pair"
    await auth_service.store_login_challenge(
        fake_redis_setup,
        email,
        old_code,
        old_magic,
        auth_generation=user.auth_generation,
    )

    monkeypatch.setattr(auth_service, "get_password_hash", lambda _password: "recovery-hash")
    recovered, created = await auth_service.recover_admin_account(
        db_session, email, "replacement-password"
    )
    assert created is False
    assert recovered.auth_generation == 1
    await db_session.commit()

    stale = await client.post(
        "/api/auth/verify-code",
        json={"email": email, "code": old_code},
    )
    assert stale.status_code == 400
    assert stale.json()["detail"] == "Invalid or expired verification code"

    with patch("app.routers.auth.send_verification_email", new_callable=AsyncMock) as send:
        issued = await client.post("/api/auth/request-code", json={"email": email})
    assert issued.status_code == 200
    assert fake_redis_setup.data[f"auth:challenge_gen:{email}"] == b"1"
    new_code = send.call_args[0][1]

    fresh = await client.post(
        "/api/auth/verify-code",
        json={"email": email, "code": new_code},
    )
    assert fresh.status_code == 200
    assert fresh.json()["user"]["role"] == UserRole.BUREAU.value


async def test_offline_recovery_invalidates_pre_recovery_magic_link(
    client,
    db_session,
    fake_redis_setup,
    monkeypatch,
) -> None:
    email = "recover-magic-boundary@example.com"
    user = User(
        id=uuid.uuid4(),
        email=email,
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
    )
    db_session.add_all([user, AllowedDomain(domain="example.com", auto_approve=True)])
    await db_session.flush()

    old_magic = "pre-recovery-magic-token"
    await auth_service.store_login_challenge(
        fake_redis_setup,
        email,
        "N2P3Q4R5",
        old_magic,
        auth_generation=user.auth_generation,
    )

    monkeypatch.setattr(auth_service, "get_password_hash", lambda _password: "recovery-hash")
    recovered, created = await auth_service.recover_admin_account(
        db_session, email, "replacement-password"
    )
    assert created is False
    assert recovered.auth_generation == 1
    await db_session.commit()

    stale = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": old_magic},
    )
    assert stale.status_code == 400
    assert stale.json()["detail"] == "Invalid or expired magic link"

    with patch("app.routers.auth.send_verification_email", new_callable=AsyncMock) as send:
        issued = await client.post("/api/auth/request-code", json={"email": email})
    assert issued.status_code == 200
    assert fake_redis_setup.data[f"auth:challenge_gen:{email}"] == b"1"
    new_magic = send.call_args[0][2].split("token=", 1)[1]

    fresh = await client.post(
        "/api/auth/verify-magic-link",
        json={"token": new_magic},
    )
    assert fresh.status_code == 200
    assert fresh.json()["user"]["role"] == UserRole.BUREAU.value
