import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole


# Helper to create auth headers
def _auth(user_id: uuid.UUID, role: str, email: str) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user_id), role, email)
    return {"Authorization": f"Bearer {token}"}


async def _make_admin(db: AsyncSession) -> dict:
    from app.models.user import User

    admin = User(
        id=uuid.uuid4(),
        email=f"admin_{uuid.uuid4().hex[:6]}@test.com",
        display_name="Admin",
        role=UserRole.BUREAU,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(admin)
    await db.flush()
    return {"user": admin, "headers": _auth(admin.id, admin.role.value, admin.email)}


@pytest.mark.asyncio
async def test_get_full_auth_config_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/admin/auth-config returns all fields derived from settings."""
    from app.config import settings

    admin_data = await _make_admin(db_session)

    with (
        patch.object(settings, "smtp_host", "mail.test.com"),
        patch.object(settings, "s3_bucket", "my-test-bucket"),
        patch.object(settings, "max_file_size_mb", 42),
        patch.object(settings, "allowed_extensions", ".pdf,.png"),
    ):
        r = await client.get("/api/admin/auth-config", headers=admin_data["headers"])

    assert r.status_code == 200
    data = r.json()

    # Check SMTP
    assert data["smtp_host"] == "mail.test.com"
    assert "smtp_port" in data

    # Check S3
    assert data["s3_bucket"] == "my-test-bucket"
    assert "s3_use_ssl" in data

    # Check Files
    assert data["max_file_size_mb"] == 42
    assert data["allowed_extensions"] == ".pdf,.png"
    assert "max_image_size_mb" in data
    assert "pdf_quality" in data


@pytest.mark.asyncio
async def test_patch_auth_config_method_not_allowed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH /api/admin/auth-config no longer exists — config is env-only."""
    admin_data = await _make_admin(db_session)
    r = await client.patch(
        "/api/admin/auth-config",
        json={"allow_all_domains": True},
        headers=admin_data["headers"],
    )
    assert r.status_code == 405
