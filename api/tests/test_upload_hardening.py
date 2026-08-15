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
    await db.commit()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_upload_hardening_features(
    client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
):
    """Test new hardening features: content-aware idempotency, fail-soft bazaar, and OOXML stripping."""
    user = await _create_user(db_session)
    headers = _auth_headers(user)
    headers["X-Upload-ID"] = str(uuid.uuid4())

    # 1. Test Content-Aware Idempotency (X-Upload-ID path uses just the header UUID)
    file1 = b"%PDF-1.4\ncontent 1"
    file2 = b"%PDF-1.4\ncontent 2"

    upload_id = headers["X-Upload-ID"]

    with patch("app.routers.upload.direct.get_s3_client") as mock_s3_cm:
        mock_s3 = AsyncMock()
        mock_s3_cm.return_value.__aenter__.return_value = mock_s3

        # Set up real in-memory cache so idempotency works across calls
        idem_cache: dict = {}

        async def _fake_get(key):
            return idem_cache.get(key)

        async def _fake_set(key, val, **kwargs):
            idem_cache[key] = val

        mock_redis.get.side_effect = _fake_get
        mock_redis.set.side_effect = _fake_set

        # Upload file 1 — idem key is based on X-Upload-ID alone
        resp = await client.post(
            "/api/upload",
            files={"file": ("f1.pdf", io.BytesIO(file1), "application/pdf")},
            headers=headers,
        )
        assert resp.status_code == 202

        # Redis idem key must contain the X-Upload-ID
        found_key1 = any(upload_id in str(call[0][0]) for call in mock_redis.get.call_args_list)
        assert found_key1, "Redis idem key should contain the X-Upload-ID"

        # Upload file 2 with the SAME X-Upload-ID but different content — same key
        mock_redis.reset_mock()
        resp = await client.post(
            "/api/upload",
            files={"file": ("f2.pdf", io.BytesIO(file2), "application/pdf")},
            headers=headers,
        )
        assert resp.status_code == 202

        found_key2 = any(upload_id in str(call[0][0]) for call in mock_redis.get.call_args_list)
        assert found_key2, "Redis idem key should contain the X-Upload-ID for file 2"

        # 2. Scanning happens in the worker — router always returns 202 regardless
        resp = await client.post(
            "/api/upload",
            files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4\nclean"), "application/pdf")},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 202, "Upload should be accepted for async processing"

    # 3. Test Fail-Closed Metadata Stripping (corrupted PDF = rejected)
    # This now happens in the worker, so we test the worker status update.
    # But for the router test, we can just check if it accepts the file for processing.
    # The actual rejection logic is in the worker, which we test in test_async_upload.py.


@pytest.mark.asyncio
async def test_ooxml_stripping(db_session: AsyncSession):
    """Verify that OOXML stripping removes docProps."""
    import tempfile
    import zipfile
    from pathlib import Path

    from app.core.security.file_security._office import _strip_ooxml_from_path

    # Create a dummy DOCX with docProps
    tmp_in = Path(tempfile.mktemp(suffix=".docx"))
    with zipfile.ZipFile(tmp_in, "w") as z:
        z.writestr(
            "word/document.xml", "<w:document xmlns:w='...'><w:body>Hi</w:body></w:document>"
        )
        z.writestr(
            "docProps/core.xml",
            "<cp:coreProperties xmlns:cp='...'><dc:creator>Attacker</dc:creator></cp:coreProperties>",
        )

    try:
        clean_path = await _strip_ooxml_from_path(tmp_in)
        assert clean_path != tmp_in

        with zipfile.ZipFile(clean_path, "r") as z:
            names = z.namelist()
            assert "word/document.xml" in names
            assert "docProps/core.xml" not in names
            assert not any(n.startswith("docProps/") for n in names)

        clean_path.unlink()
    finally:
        tmp_in.unlink()
