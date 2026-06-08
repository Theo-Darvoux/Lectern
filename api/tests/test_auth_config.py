"""Tests for auth config and PENDING approval flow."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_config import AllowedDomain
from app.models.user import User, UserRole

# ── helpers ───────────────────────────────────────────────────────────────────


async def _make_user(db: AsyncSession, role: UserRole, email_prefix: str = "") -> User:
    prefix = email_prefix or role.value
    user = User(
        id=uuid.uuid4(),
        email=f"{prefix}_{uuid.uuid4().hex[:6]}@example.com",
        display_name=role.value.capitalize(),
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_domain(db: AsyncSession, domain: str, auto_approve: bool = True) -> AllowedDomain:
    row = AllowedDomain(domain=domain, auto_approve=auto_approve)
    db.add(row)
    await db.flush()
    return row


def _auth(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


# ── auth-config GET/PATCH ─────────────────────────────────────────────────────


async def test_get_auth_config_defaults(client: AsyncClient, db_session: AsyncSession) -> None:
    """Empty DB → empty domain list with default auth flags."""
    admin = await _make_user(db_session, UserRole.BUREAU)
    r = await client.get("/api/admin/auth-config", headers=_auth(admin))
    assert r.status_code == 200
    data = r.json()
    assert data["totp_enabled"] is True
    assert data["google_oauth_enabled"] is False
    assert data["allow_all_domains"] is False
    assert isinstance(data["domains"], list)


# ── allowed domains CRUD ──────────────────────────────────────────────────────


async def test_list_domains_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    r = await client.get("/api/admin/auth-config/domains", headers=_auth(admin))
    assert r.status_code == 200
    assert r.json() == []


async def test_add_domain(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    r = await client.post(
        "/api/admin/auth-config/domains",
        json={"domain": "newschool.fr", "auto_approve": True},
        headers=_auth(admin),
    )
    assert r.status_code == 201
    data = r.json()
    assert data["domain"] == "newschool.fr"
    assert data["auto_approve"] is True
    assert "id" in data


async def test_add_domain_strips_at_sign(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    r = await client.post(
        "/api/admin/auth-config/domains",
        json={"domain": "@example.com"},
        headers=_auth(admin),
    )
    assert r.status_code == 201
    assert r.json()["domain"] == "example.com"


async def test_add_domain_duplicate_conflict(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    await _seed_domain(db_session, "dupe.edu")
    r = await client.post(
        "/api/admin/auth-config/domains",
        json={"domain": "dupe.edu"},
        headers=_auth(admin),
    )
    assert r.status_code == 409


async def test_update_domain_auto_approve(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    domain = await _seed_domain(db_session, "school.fr", auto_approve=True)
    r = await client.patch(
        f"/api/admin/auth-config/domains/{domain.id}",
        json={"auto_approve": False},
        headers=_auth(admin),
    )
    assert r.status_code == 200
    assert r.json()["auto_approve"] is False


async def test_delete_domain(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    domain = await _seed_domain(db_session, "todelete.edu")
    r = await client.delete(
        f"/api/admin/auth-config/domains/{domain.id}",
        headers=_auth(admin),
    )
    assert r.status_code == 200

    r2 = await client.get("/api/admin/auth-config/domains", headers=_auth(admin))
    assert all(d["domain"] != "todelete.edu" for d in r2.json())


async def test_delete_domain_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    r = await client.delete(
        f"/api/admin/auth-config/domains/{uuid.uuid4()}",
        headers=_auth(admin),
    )
    assert r.status_code == 404


# ── email domain validation ───────────────────────────────────────────────────


async def test_request_code_allowed_domain(client: AsyncClient, db_session: AsyncSession) -> None:
    """A configured allowed domain passes validation."""
    await _seed_domain(db_session, "example.com")
    r = await client.post(
        "/api/auth/request-code",
        json={"email": "student@example.com"},
    )
    # Email sending will fail in test env, but domain validation passes
    assert r.status_code == 200


async def test_request_code_disallowed_domain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """gmail.com is not in allowed list → 400."""
    r = await client.post(
        "/api/auth/request-code",
        json={"email": "hacker@gmail.com"},
    )
    assert r.status_code == 400


async def test_request_code_open_registration(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """With allow_all_domains=True, any email is allowed."""
    from app.config import settings

    with patch.object(settings, "allow_all_domains", True):
        r = await client.post(
            "/api/auth/request-code",
            json={"email": "anyone@gmail.com"},
        )
    assert r.status_code == 200


async def test_request_code_newly_added_domain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Domains added via admin API are immediately honoured."""
    await _seed_domain(db_session, "newuni.ac.uk")
    r = await client.post(
        "/api/auth/request-code",
        json={"email": "user@newuni.ac.uk"},
    )
    assert r.status_code == 200


async def test_request_code_removed_domain_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No domains in DB and no allow_all_domains → unknown domain rejected."""
    r = await client.post(
        "/api/auth/request-code",
        json={"email": "user@unknown.io"},
    )
    assert r.status_code == 400


# ── PENDING role & approval flow ──────────────────────────────────────────────


async def test_pending_user_blocked(client: AsyncClient, db_session: AsyncSession) -> None:
    """PENDING user cannot access protected endpoints."""
    pending = User(
        id=uuid.uuid4(),
        email=f"pending_{uuid.uuid4().hex[:6]}@school.fr",
        role=UserRole.PENDING,
        onboarded=False,
        gdpr_consent=False,
    )
    db_session.add(pending)
    await db_session.flush()

    from app.core.security import create_access_token

    token, _ = create_access_token(str(pending.id), pending.role.value, pending.email)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.get("/api/users/me", headers=headers)
    assert r.status_code == 403
    assert r.json().get("error_code") == "USER_PENDING"


async def test_approve_pending_user(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    pending = User(
        id=uuid.uuid4(),
        email=f"pending_{uuid.uuid4().hex[:6]}@school.fr",
        role=UserRole.PENDING,
        onboarded=False,
        gdpr_consent=False,
    )
    db_session.add(pending)
    await db_session.flush()

    r = await client.post(
        f"/api/admin/users/{pending.id}/approve",
        headers=_auth(admin),
    )
    assert r.status_code == 200
    assert r.json()["role"] == "student"

    await db_session.refresh(pending)
    assert pending.role == UserRole.STUDENT


async def test_approve_non_pending_user_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    student = await _make_user(db_session, UserRole.STUDENT)

    r = await client.post(
        f"/api/admin/users/{student.id}/approve",
        headers=_auth(admin),
    )
    assert r.status_code == 400


async def test_reject_pending_user(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    pending = User(
        id=uuid.uuid4(),
        email=f"reject_{uuid.uuid4().hex[:6]}@school.fr",
        role=UserRole.PENDING,
        onboarded=False,
        gdpr_consent=False,
    )
    db_session.add(pending)
    await db_session.flush()
    pending_id = pending.id

    r = await client.post(
        f"/api/admin/users/{pending_id}/reject",
        headers=_auth(admin),
    )
    assert r.status_code == 200

    from sqlalchemy import select

    from app.models.user import User as UserModel

    remaining = await db_session.scalar(select(UserModel).where(UserModel.id == pending_id))
    assert remaining is None


async def test_reject_non_pending_user_error(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    mod = await _make_user(db_session, UserRole.MODERATOR)

    r = await client.post(
        f"/api/admin/users/{mod.id}/reject",
        headers=_auth(admin),
    )
    assert r.status_code == 400


async def test_new_user_gets_pending_role_when_domain_not_auto_approve(
    client: AsyncClient, db_session: AsyncSession, mock_redis, fake_redis_setup
) -> None:
    """New user logging in via a non-auto-approve domain becomes PENDING."""
    from unittest.mock import AsyncMock, patch

    from app.config import settings

    # Seed: domain exists but auto_approve=False
    await _seed_domain(db_session, "manual.edu", auto_approve=False)

    # Put a valid OTP-format code in fake redis (must match ^[A-Z2-9]{8}$)
    email = "newstudent@manual.edu"
    fake_redis_setup.data[f"auth:code:{email}"] = b"TESTCODE"

    original_env = settings.environment
    settings.environment = "development"
    try:
        with patch("app.services.email.send_verification_email", new_callable=AsyncMock):
            # Use dev-bypass sentinel "AAAAAAAA" which is valid per schema pattern
            r = await client.post(
                "/api/auth/verify-code",
                json={"email": email, "code": "AAAAAAAA"},
            )
    finally:
        settings.environment = original_env

    assert r.status_code == 200
    assert r.json()["user"]["role"] == "pending"
