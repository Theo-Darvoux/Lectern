"""Guest (read-only visitor) access: session issuance and read-only enforcement."""

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.auth_config import AuthConfig
from app.models.user import User, UserRole


def _guest_headers(guest: User) -> dict[str, str]:
    token, _ = create_access_token(str(guest.id), guest.role.value, guest.email)
    return {"Authorization": f"Bearer {token}"}


async def _create_guest_user(db: AsyncSession) -> User:
    guest = User(
        id=uuid.uuid4(),
        email="guest@wikint.local",
        display_name="Guest",
        role=UserRole.GUEST,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(guest)
    await db.flush()
    return guest


async def _create_student(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@telecom-sudparis.eu",
        display_name="Student",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _enable_guest_access(db: AsyncSession) -> None:
    db.add(AuthConfig(guest_access_enabled=True))
    await db.flush()


# --------------------------------------------------------------------------- #
#  /auth/methods + /auth/guest                                                 #
# --------------------------------------------------------------------------- #


async def test_auth_methods_exposes_guest_flag_default_false(client: AsyncClient):
    resp = await client.get("/api/auth/methods")
    assert resp.status_code == 200
    assert resp.json()["guest_access_enabled"] is False


async def test_guest_session_rejected_when_disabled(client: AsyncClient, db_session: AsyncSession):
    # A guest identity exists but the admin toggle is off.
    await _create_guest_user(db_session)
    resp = await client.post("/api/auth/guest")
    assert resp.status_code == 401


async def test_guest_session_issued_when_enabled(client: AsyncClient, db_session: AsyncSession):
    await _create_guest_user(db_session)
    await _enable_guest_access(db_session)

    resp = await client.post("/api/auth/guest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["role"] == "guest"
    assert body["user"]["display_name"] == "Guest"
    assert body["access_token"]
    assert body["is_new_user"] is False


async def test_guest_session_unavailable_without_seeded_guest(
    client: AsyncClient, db_session: AsyncSession
):
    # Toggle on, but no guest identity seeded -> cannot start a session.
    await _enable_guest_access(db_session)
    resp = await client.post("/api/auth/guest")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
#  Read-only enforcement (the central chokepoint)                              #
# --------------------------------------------------------------------------- #


async def test_guest_can_read(client: AsyncClient, db_session: AsyncSession):
    guest = await _create_guest_user(db_session)
    resp = await client.get("/api/users/me", headers=_guest_headers(guest))
    assert resp.status_code == 200
    assert resp.json()["role"] == "guest"


async def test_guest_write_blocked_on_like(client: AsyncClient, db_session: AsyncSession):
    guest = await _create_guest_user(db_session)
    # The material need not exist: the guest read-only guard fires in the auth
    # dependency, before the endpoint body runs.
    resp = await client.post(f"/api/materials/{uuid.uuid4()}/like", headers=_guest_headers(guest))
    assert resp.status_code == 403
    assert resp.json().get("error_code") == "GUEST_READ_ONLY"


async def test_guest_write_blocked_on_comment(client: AsyncClient, db_session: AsyncSession):
    guest = await _create_guest_user(db_session)
    resp = await client.post(
        "/api/comments",
        headers=_guest_headers(guest),
        json={
            "target_type": "material",
            "target_id": str(uuid.uuid4()),
            "body": "hello",
        },
    )
    assert resp.status_code == 403
    assert resp.json().get("error_code") == "GUEST_READ_ONLY"


async def test_guest_write_blocked_on_profile_update(client: AsyncClient, db_session: AsyncSession):
    guest = await _create_guest_user(db_session)
    resp = await client.patch(
        "/api/users/me",
        headers=_guest_headers(guest),
        json={"display_name": "Hacker"},
    )
    assert resp.status_code == 403
    assert resp.json().get("error_code") == "GUEST_READ_ONLY"


async def test_student_self_write_succeeds(client: AsyncClient, db_session: AsyncSession):
    # The same write that a guest is blocked from succeeds for a real student,
    # proving the guard targets guests specifically and not the endpoint.
    student = await _create_student(db_session)
    token, _ = create_access_token(str(student.id), student.role.value, student.email)
    resp = await client.patch(
        "/api/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "Renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Renamed"


# --------------------------------------------------------------------------- #
#  Pull requests are fully hidden from guests                                  #
# --------------------------------------------------------------------------- #


async def test_guest_forbidden_from_pr_list(client: AsyncClient, db_session: AsyncSession):
    guest = await _create_guest_user(db_session)
    resp = await client.get("/api/pull-requests", headers=_guest_headers(guest))
    assert resp.status_code == 403
    assert resp.json().get("error_code") == "GUEST_FORBIDDEN"


async def test_student_allowed_on_pr_list(client: AsyncClient, db_session: AsyncSession):
    student = await _create_student(db_session)
    token, _ = create_access_token(str(student.id), student.role.value, student.email)
    resp = await client.get("/api/pull-requests", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
