from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.config import settings
from app.core.security.security import (
    BROWSER_READ_COOKIE,
    create_access_token,
    create_browser_read_token,
    decode_token,
)
from app.dependencies.rate_limit import _rate_limit_subject
from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
from app.models.user import User, UserRole
from app.schemas.pull_request import CreateDirectoryOp, EditDirectoryOp
from app.services.directory import _validate_zip_arcname

REPO_ROOT = Path(__file__).resolve().parents[2]


async def _user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Hardening test",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _material(db: AsyncSession, owner: User) -> tuple[Directory, Material]:
    directory = Directory(
        id=uuid.uuid4(),
        name="Safe",
        slug=f"safe-{uuid.uuid4().hex[:6]}",
        type="folder",
        created_by=owner.id,
    )
    db.add(directory)
    await db.flush()
    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Material",
        slug=f"material-{uuid.uuid4().hex[:6]}",
        type="document",
        author_id=owner.id,
        current_version=1,
    )
    db.add(material)
    await db.flush()
    db.add(
        MaterialVersion(
            id=uuid.uuid4(),
            material_id=material.id,
            version_number=1,
            file_key="cas/safe",
            file_name="safe.pdf",
            file_size=1,
            file_mime_type="application/pdf",
        )
    )
    await db.flush()
    return directory, material


def _bearer(user: User, *, session_id: str = "session-a") -> dict[str, str]:
    token, _ = create_access_token(
        str(user.id),
        user.role.value,
        user.email,
        session_id=session_id,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("name", [".", "..", "../escape", "a/b", r"a\\b", "C:", "C:escape"])
def test_directory_create_rejects_archive_path_components(name: str) -> None:
    with pytest.raises(ValidationError):
        CreateDirectoryOp(name=name)


@pytest.mark.parametrize("name", [".", "..", "../escape", "a/b", r"a\\b", "C:", "C:escape"])
def test_directory_edit_rejects_archive_path_components(name: str) -> None:
    with pytest.raises(ValidationError):
        EditDirectoryOp(directory_id=uuid.uuid4(), name=name)


def test_directory_type_contract_comes_from_orm_enum() -> None:
    assert CreateDirectoryOp(name="Module", type="module").type == "module"
    assert CreateDirectoryOp(name="Folder", type="folder").type == "folder"
    for unsupported in ("course", "year", "semester", "other"):
        with pytest.raises(ValidationError):
            CreateDirectoryOp(name="Nope", type=unsupported)


@pytest.mark.parametrize(
    "arcname",
    [
        "../escape.pdf",
        "safe/../escape.pdf",
        "/absolute.pdf",
        r"safe\\escape.pdf",
        "C:/drive.pdf",
        "C:drive-relative.pdf",
        "safe//double.pdf",
    ],
)
def test_zip_arcname_defense_rejects_unsafe_legacy_paths(arcname: str) -> None:
    with pytest.raises(ValueError):
        _validate_zip_arcname(arcname)


def test_zip_arcname_defense_accepts_safe_relative_path() -> None:
    assert _validate_zip_arcname("module/week-1/notes.pdf") == "module/week-1/notes.pdf"


async def test_annotation_reads_require_authentication(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session)
    _directory, material = await _material(db_session, owner)
    await db_session.commit()

    anonymous = await client.get(f"/api/materials/{material.id}/annotations")
    assert anonymous.status_code == 401

    bearer = await client.get(
        f"/api/materials/{material.id}/annotations",
        headers=_bearer(owner),
    )
    assert bearer.status_code == 200

    browser_cookie = create_browser_read_token(
        str(owner.id),
        session_id="browser-session",
    )
    cookie_read = await client.get(
        f"/api/materials/{material.id}/annotations",
        cookies={BROWSER_READ_COOKIE: browser_cookie},
    )
    assert cookie_read.status_code == 200


async def test_pending_user_cannot_read_annotations(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session)
    pending = await _user(db_session, UserRole.PENDING)
    _directory, material = await _material(db_session, owner)
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{material.id}/annotations",
        headers=_bearer(pending),
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "USER_PENDING"


async def test_material_and_directory_sse_reject_anonymous_clients(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session)
    directory, material = await _material(db_session, owner)
    await db_session.commit()

    assert (await client.get(f"/api/materials/{material.id}/sse")).status_code == 401
    assert (await client.get(f"/api/directories/{directory.id}/sse")).status_code == 401


def _request_with_bearer(token: str, client_ip: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/search",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": (client_ip, 443),
            "server": ("test", 443),
        }
    )


def test_guest_rate_limits_are_per_session_not_shared_user() -> None:
    guest = User(
        id=uuid.uuid4(),
        email="guest@lectern.local",
        role=UserRole.GUEST,
        onboarded=True,
    )
    token_a, _ = create_access_token(
        str(guest.id), guest.role.value, guest.email, session_id="guest-a"
    )
    token_b, _ = create_access_token(
        str(guest.id), guest.role.value, guest.email, session_id="guest-b"
    )
    subject_a = _rate_limit_subject(_request_with_bearer(token_a), guest)
    subject_b = _rate_limit_subject(_request_with_bearer(token_b), guest)
    assert subject_a == "guest-session:guest-a"
    assert subject_b == "guest-session:guest-b"
    assert subject_a != subject_b


def test_session_family_is_shared_across_token_types() -> None:
    access, _ = create_access_token(
        "user-id",
        "student",
        "user@example.com",
        session_id="family-1",
    )
    browser = create_browser_read_token("user-id", session_id="family-1")
    assert decode_token(access, expected_type="access")["sid"] == "family-1"
    assert decode_token(browser, expected_type="browser_read")["sid"] == "family-1"


async def test_metrics_token_is_header_only(client: AsyncClient) -> None:
    with patch.object(settings, "metrics_token", "metrics-secret"):
        query_only = await client.get("/metrics?token=metrics-secret")
        assert query_only.status_code == 403

        header = await client.get(
            "/metrics",
            headers={"Authorization": "Bearer metrics-secret"},
        )
        assert header.status_code == 200


def test_magic_link_capability_never_uses_request_query() -> None:
    backend = (REPO_ROOT / "api/app/routers/auth.py").read_text(encoding="utf-8")
    frontend = (REPO_ROOT / "web/src/app/login/verify/page.tsx").read_text(encoding="utf-8")
    assert "/login/verify?token=" not in backend
    assert "/login/verify#token=" in backend
    assert "useSearchParams" not in frontend
    assert "window.location.hash" in frontend
    assert 'searchParams.delete("token")' in frontend
    assert "window.history.replaceState" in frontend


def test_refresh_route_uses_atomic_consume_and_session_family_replay_revoke() -> None:
    source = (REPO_ROOT / "api/app/routers/auth.py").read_text(encoding="utf-8")
    refresh = source.split("async def refresh_token(", 1)[1].split('@router.post("/logout"', 1)[0]
    assert "consume_token_once" in refresh
    assert "revoke_session" in refresh
    assert "is_token_blacklisted" not in refresh
