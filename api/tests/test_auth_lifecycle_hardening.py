from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.common.exceptions import UnauthorizedError
from app.core.security.security import (
    create_access_token,
    create_browser_read_token,
    create_refresh_token,
    decode_token,
)
from app.dependencies.auth import _validate_token_payload
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
    assert decode_token(access, expected_type="access")["sid"] == decode_token(
        refresh, expected_type="refresh"
    )["sid"]


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


def test_production_magic_link_paths_use_atomic_fragment_only_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    router = (root / "api/app/routers/auth.py").read_text(encoding="utf-8")
    cli = (root / "api/app/cli.py").read_text(encoding="utf-8")

    assert "store_login_challenge" in router
    assert "store_login_challenge" in cli
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
