import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_config import AuthConfig
from app.models.user import UserRole


# Helper to create auth headers
def _auth(user_id: uuid.UUID, role: str, email: str) -> dict[str, str]:
    from app.core.security import create_access_token

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
    """Verify that GET /api/admin/auth-config returns all fields including storage and file settings."""
    admin_data = await _make_admin(db_session)

    # Pre-seed some data
    config = AuthConfig(
        smtp_host="mail.test.com",
        s3_bucket="my-test-bucket",
        max_file_size_mb=42,
        allowed_extensions=".pdf,.png",
    )
    db_session.add(config)
    await db_session.commit()

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
async def test_patch_storage_settings(client: AsyncClient, db_session: AsyncSession) -> None:
    """Verify patching S3 storage settings; secrets are redacted in API responses."""
    admin_data = await _make_admin(db_session)

    patch_data = {
        "s3_endpoint": "http://minio:9000",
        "s3_access_key": "minioadmin",
        "s3_secret_key": "minioadmin",
        "s3_bucket": "wikint",
        "s3_use_ssl": False,
        "s3_region": "us-east-1",
    }

    r = await client.patch("/api/admin/auth-config", json=patch_data, headers=admin_data["headers"])
    assert r.status_code == 200
    data = r.json()

    secret_fields = {"s3_access_key", "s3_secret_key"}
    for key, val in patch_data.items():
        if key in secret_fields:
            assert data.get(f"{key}_set") is True, f"{key}_set must be True after setting"
            assert key not in data, f"Secret {key} must not appear in API response"
        else:
            assert data[key] == val


@pytest.mark.asyncio
async def test_patch_file_limits_and_quality(client: AsyncClient, db_session: AsyncSession) -> None:
    """Verify patching file size limits and quality settings."""
    admin_data = await _make_admin(db_session)

    patch_data = {
        "max_file_size_mb": 50,
        "max_image_size_mb": 5,
        "max_video_size_mb": 500,
        "pdf_quality": 60,
        "video_compression_profile": "fast",
        "allowed_extensions": "pdf,png,jpg",
        "allowed_mime_types": "application/pdf,image/png",
    }

    r = await client.patch("/api/admin/auth-config", json=patch_data, headers=admin_data["headers"])
    assert r.status_code == 200
    data = r.json()

    assert data["max_file_size_mb"] == 50
    assert data["max_image_size_mb"] == 5
    assert data["pdf_quality"] == 60
    assert data["video_compression_profile"] == "fast"
    assert data["allowed_extensions"] == "pdf,png,jpg"
    assert data["allowed_mime_types"] == "application/pdf,image/png"


@pytest.mark.asyncio
async def test_patch_smtp_settings(client: AsyncClient, db_session: AsyncSession) -> None:
    """Verify patching SMTP settings."""
    admin_data = await _make_admin(db_session)

    patch_data = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "test@gmail.com",
        "smtp_password": "securepassword",
        "smtp_use_tls": True,
    }

    r = await client.patch("/api/admin/auth-config", json=patch_data, headers=admin_data["headers"])
    assert r.status_code == 200
    data = r.json()

    assert data["smtp_host"] == "smtp.gmail.com"
    assert data["smtp_port"] == 587
    assert data["smtp_user"] == "test@gmail.com"
    # smtp_password is a secret — verified via _set flag, not by value
    assert data.get("smtp_password_set") is True
    assert "smtp_password" not in data
    assert data["smtp_use_tls"] is True


@pytest.mark.asyncio
async def test_patch_nullifies_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    """Verify that sending None/null clears the fields in the DB."""
    admin_data = await _make_admin(db_session)

    # 1. First set it
    await client.patch(
        "/api/admin/auth-config", json={"smtp_host": "some-host.com"}, headers=admin_data["headers"]
    )

    # 2. Then clear it
    r = await client.patch(
        "/api/admin/auth-config", json={"smtp_host": None}, headers=admin_data["headers"]
    )
    assert r.status_code == 200
    from app.config import settings

    assert r.json()["smtp_host"] == settings.smtp_host


@pytest.mark.asyncio
async def test_patch_branding_settings(client: AsyncClient, db_session: AsyncSession) -> None:
    """Verify patching branding settings."""
    admin_data = await _make_admin(db_session)

    patch_data = {
        "site_name": "New Wiki Name",
        "site_description": "Custom Description",
        "site_logo_url": "https://img.com/logo.png",
        "site_favicon_url": "https://img.com/fav.ico",
        "primary_color": "#FF0000",
        "footer_text": "Custom Footer 2024",
        "organization_url": "https://org.com",
    }

    r = await client.patch("/api/admin/auth-config", json=patch_data, headers=admin_data["headers"])
    assert r.status_code == 200
    data = r.json()

    for key, val in patch_data.items():
        assert data[key] == val
