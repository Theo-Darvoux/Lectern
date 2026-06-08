"""Tests for role-based access control on /api/moderator and /api/admin routes."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


async def _make_user(db: AsyncSession, role: UserRole) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{role.value}_{uuid.uuid4().hex[:6]}@example.com",
        display_name=role.value.capitalize(),
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


def _auth(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


# ── /api/moderator routes ─────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX])
async def test_moderator_stats_allowed(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    user = await _make_user(db_session, role)
    r = await client.get("/api/moderator/stats", headers=_auth(user))
    assert r.status_code == 200
    data = r.json()
    assert "user_count" in data
    assert "material_count" in data


async def test_moderator_stats_student_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _make_user(db_session, UserRole.STUDENT)
    r = await client.get("/api/moderator/stats", headers=_auth(user))
    assert r.status_code == 403


async def test_moderator_stats_unauthenticated(client: AsyncClient) -> None:
    r = await client.get("/api/moderator/stats")
    assert r.status_code == 401


@pytest.mark.parametrize("role", [UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX])
async def test_moderator_directories_allowed(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    user = await _make_user(db_session, role)
    r = await client.get("/api/moderator/directories", headers=_auth(user))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_moderator_directories_student_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _make_user(db_session, UserRole.STUDENT)
    r = await client.get("/api/moderator/directories", headers=_auth(user))
    assert r.status_code == 403


@pytest.mark.parametrize("role", [UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX])
async def test_moderator_featured_list_allowed(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    user = await _make_user(db_session, role)
    r = await client.get("/api/moderator/featured", headers=_auth(user))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_moderator_featured_student_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _make_user(db_session, UserRole.STUDENT)
    r = await client.get("/api/moderator/featured", headers=_auth(user))
    assert r.status_code == 403


# ── /api/admin routes (BUREAU/VIEUX only) ────────────────────────────────────


@pytest.mark.parametrize("role", [UserRole.BUREAU, UserRole.VIEUX])
async def test_admin_users_list_allowed(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    user = await _make_user(db_session, role)
    r = await client.get("/api/admin/users", headers=_auth(user))
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "total" in data


async def test_admin_users_list_moderator_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _make_user(db_session, UserRole.MODERATOR)
    r = await client.get("/api/admin/users", headers=_auth(user))
    assert r.status_code == 403


async def test_admin_users_list_student_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _make_user(db_session, UserRole.STUDENT)
    r = await client.get("/api/admin/users", headers=_auth(user))
    assert r.status_code == 403


@pytest.mark.parametrize("role", [UserRole.BUREAU, UserRole.VIEUX])
async def test_admin_update_role_allowed(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    admin = await _make_user(db_session, role)
    target = await _make_user(db_session, UserRole.STUDENT)
    r = await client.patch(
        f"/api/admin/users/{target.id}/role?role=moderator",
        headers=_auth(admin),
    )
    assert r.status_code == 200
    assert r.json()["role"] == "moderator"


async def test_admin_update_role_moderator_forbidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    mod = await _make_user(db_session, UserRole.MODERATOR)
    target = await _make_user(db_session, UserRole.STUDENT)
    r = await client.patch(
        f"/api/admin/users/{target.id}/role?role=moderator",
        headers=_auth(mod),
    )
    assert r.status_code == 403


async def test_admin_update_role_invalid_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    target = await _make_user(db_session, UserRole.STUDENT)
    r = await client.patch(
        f"/api/admin/users/{target.id}/role?role=superadmin",
        headers=_auth(admin),
    )
    assert r.status_code == 400


async def test_admin_update_role_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    fake_id = uuid.uuid4()
    r = await client.patch(
        f"/api/admin/users/{fake_id}/role?role=moderator",
        headers=_auth(admin),
    )
    assert r.status_code == 404


@pytest.mark.parametrize("role", [UserRole.BUREAU, UserRole.VIEUX])
async def test_admin_dlq_list_allowed(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    admin = await _make_user(db_session, role)
    r = await client.get("/api/admin/dlq", headers=_auth(admin))
    assert r.status_code == 200
    data = r.json()
    assert "items" in data


async def test_admin_dlq_moderator_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    mod = await _make_user(db_session, UserRole.MODERATOR)
    r = await client.get("/api/admin/dlq", headers=_auth(mod))
    assert r.status_code == 403


async def test_admin_dlq_student_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _make_user(db_session, UserRole.STUDENT)
    r = await client.get("/api/admin/dlq", headers=_auth(student))
    assert r.status_code == 403


async def test_admin_dlq_retry_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    fake_id = uuid.uuid4()
    r = await client.post(f"/api/admin/dlq/{fake_id}/retry", headers=_auth(admin))
    assert r.status_code == 404


async def test_admin_dlq_dismiss_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, UserRole.BUREAU)
    fake_id = uuid.uuid4()
    r = await client.post(f"/api/admin/dlq/{fake_id}/dismiss", headers=_auth(admin))
    assert r.status_code == 404


# ── Old /api/admin endpoints now return 404 (removed) ────────────────────────


async def test_old_admin_stats_gone(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/admin/stats was moved to /api/moderator/stats."""
    admin = await _make_user(db_session, UserRole.BUREAU)
    r = await client.get("/api/admin/stats", headers=_auth(admin))
    assert r.status_code == 404


async def test_old_admin_directories_gone(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/admin/directories was moved to /api/moderator/directories."""
    admin = await _make_user(db_session, UserRole.BUREAU)
    r = await client.get("/api/admin/directories", headers=_auth(admin))
    assert r.status_code == 404
