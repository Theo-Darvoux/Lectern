import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


def _make_pdf_file(content: bytes = b"%PDF-1.4 test content") -> dict:
    """Create a multipart file tuple for httpx."""
    return {"file": ("test.pdf", io.BytesIO(content), "application/pdf")}


@patch("app.routers.upload.direct.get_s3_client")
async def test_upload_success(
    mock_s3_cm,
    client: AsyncClient,
    db_session: AsyncSession,
    mock_arq_pool: AsyncMock,
) -> None:
    """Direct upload returns 202 and enqueues async processing."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/api/upload",
        files=_make_pdf_file(),
        headers=_auth_headers(user),
    )
    assert response.status_code == 202
    data = response.json()
    assert data["mime_type"] == "application/pdf"
    assert data["size"] > 0
    assert "file_key" in data
    assert data["file_key"].startswith(f"quarantine/{user.id}/")
    assert data["status"] == "pending"

    mock_s3.upload_file.assert_called_once()
    mock_arq_pool.enqueue_job.assert_called_once()


async def test_upload_extension_not_allowed(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/api/upload",
        files={"file": ("malware.exe", io.BytesIO(b"MZ..."), "application/x-executable")},
        headers=_auth_headers(user),
    )
    assert response.status_code == 400
    assert "not supported" in response.json()["detail"]


@pytest.mark.asyncio
@patch("app.routers.upload.direct.get_s3_client")
async def test_upload_too_large(
    mock_s3_client, client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
) -> None:
    # Setup S3 mock
    mock_s3 = AsyncMock()
    mock_s3_client.return_value.__aenter__.return_value = mock_s3

    from unittest.mock import patch

    from app.config import settings

    user = await _create_user(db_session)

    # Patch document size limit to 0 MiB so any PDF is rejected
    with patch.object(settings, "max_document_size_mb", 0):
        response = await client.post(
            "/api/upload",
            files=_make_pdf_file(b"%PDF-1.4 some content here"),
            headers=_auth_headers(user),
        )
    # If this fails with 202, it means validation was bypassed.
    assert response.status_code == 400
    assert "exceeds" in response.json()["detail"].lower()


@patch("app.routers.upload.direct.get_s3_client")
async def test_upload_svg_xss_rejected(
    mock_s3_client,
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    mock_s3 = AsyncMock()
    mock_s3_client.return_value.__aenter__.return_value = mock_s3

    svg_with_script = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

    user = await _create_user(db_session)
    response = await client.post(
        "/api/upload",
        files={"file": ("image.svg", io.BytesIO(svg_with_script), "image/svg+xml")},
        headers=_auth_headers(user),
    )
    # SVG XSS check is inline — rejected before processing is enqueued.
    assert response.status_code == 400
    assert (
        "script" in response.json()["detail"].lower()
        or "active content" in response.json()["detail"].lower()
    )


async def test_deprecated_request_url(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.post(
        "/api/upload/request-url",
        json={"filename": "test.pdf", "size": 1024, "mime_type": "application/pdf"},
        headers=_auth_headers(user),
    )
    assert response.status_code == 400
    assert "removed" in response.json()["detail"]
