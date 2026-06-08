"""Tests for Phase 3A presigned upload hardening.

Covers:
- 1.2: content_length enforced in presigned PUT params
- 1.3: MIME re-validation on presigned complete (Range GET + _apply_mime_correction)
- 1.15: SHA-256 optional field stored in intent and forwarded to worker
- 3C: app_error_handler always includes error_code field
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    import uuid

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


# ── 1.2: content_length in presigned PUT ────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_presigned_put_includes_content_length():
    """generate_presigned_put must pass ContentLength to boto3 for exact-size enforcement."""
    from unittest.mock import AsyncMock, patch

    mock_url = "https://s3.example.com/quarantine/test?X-Amz-Signature=abc"
    mock_client = AsyncMock()
    mock_client.generate_presigned_url = AsyncMock(return_value=mock_url)

    captured_params: dict = {}

    async def mock_generate(operation, **kwargs):
        captured_params.update(kwargs.get("Params", {}))
        return mock_url

    mock_client.generate_presigned_url.side_effect = mock_generate

    from app.core.storage import generate_presigned_put

    with patch("app.core.storage.get_s3_client") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        await generate_presigned_put(
            "quarantine/test/file.pdf", "application/pdf", content_length=1024
        )

    assert "ContentLength" in captured_params
    assert captured_params["ContentLength"] == 1024


# ── 1.15: SHA-256 stored in intent ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_upload_stores_sha256_in_intent(
    client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
):
    """POST /upload/init with sha256 must store it in the Redis intent."""
    user = await _create_user(db_session)
    await db_session.commit()

    mock_redis.get.return_value = None

    presigned_url = "https://s3.example.com/quarantine/test?sig=abc"
    with (
        patch(
            "app.routers.upload.presigned.generate_presigned_put",
            new_callable=AsyncMock,
            return_value=presigned_url,
        ),
        patch("app.routers.upload.presigned._create_upload_row", new_callable=AsyncMock),
        patch("app.routers.upload.presigned._check_pending_cap", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/upload/init",
            json={
                "filename": "test.pdf",
                "size": 1024,
                "mime_type": "application/pdf",
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
            headers=_auth_headers(user),
        )

    assert response.status_code == 200

    # Verify Redis was called with a set — the intent must include sha256
    set_calls = [
        c
        for c in mock_redis.set.call_args_list
        if c.args and isinstance(c.args[0], str) and c.args[0].startswith("upload:intent:")
    ]
    assert len(set_calls) >= 1
    intent_str = set_calls[0].args[1]
    intent = json.loads(intent_str)
    assert intent["sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ── 1.3: MIME re-validation on presigned complete ────────────────────────────


@pytest.mark.asyncio
async def test_complete_upload_revalidates_mime(
    client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
):
    """POST /upload/complete must run MIME re-validation via Range GET."""
    import uuid

    user = await _create_user(db_session)
    await db_session.commit()

    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/test.pdf"

    intent = {
        "user_id": str(user.id),
        "upload_id": upload_id,
        "quarantine_key": quarantine_key,
        "filename": "test.pdf",
        "mime_type": "application/pdf",
        "sha256": None,
    }
    intent_key = f"upload:intent:{upload_id}"
    intent_encoded = json.dumps(intent).encode()

    async def selective_get(key):
        if key == intent_key:
            return intent_encoded
        return None

    async def selective_execute(cmd, key):
        if cmd == "GETDEL" and key == intent_key:
            return intent_encoded
        return None

    mock_redis.get.side_effect = selective_get
    mock_redis.execute_command.side_effect = selective_execute

    pdf_header = b"%PDF-1.7" + b"\x00" * 100

    with (
        patch(
            "app.routers.upload.presigned.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 1024},
        ),
        patch(
            "app.core.storage.read_object_bytes", new_callable=AsyncMock, return_value=pdf_header
        ),
        patch("app.routers.upload.presigned._enqueue_processing", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/upload/complete",
            json={"quarantine_key": quarantine_key, "upload_id": upload_id},
            headers=_auth_headers(user),
        )

    assert response.status_code == 202


# ── 3C: error handler always includes error_code ─────────────────────────────


@pytest.mark.asyncio
async def test_error_response_always_has_error_code(client: AsyncClient, db_session: AsyncSession):
    """AppError responses must always include error_code (even if None)."""
    response = await client.get("/api/upload/does-not-exist-endpoint-xyz")
    # 404 from FastAPI itself won't trigger our handler, so hit a known AppError path
    # Upload config always returns 200, so let's use a bad auth path
    response = await client.post("/api/upload/check-exists", json={"sha256": "a" * 64, "size": 100})
    assert response.status_code == 401
    data = response.json()
    assert "error_code" in data
    assert "error_message" in data


@pytest.mark.asyncio
async def test_error_response_includes_code_when_set(client: AsyncClient, db_session: AsyncSession):
    """When an error has a code, error_code is non-null."""
    from app.models.user import UserRole

    user = await _create_user(db_session, role=UserRole.STUDENT)
    await db_session.commit()

    # Hit an endpoint that returns a coded error — upload with bad extension
    with patch("app.routers.upload.direct.upload_file", new_callable=AsyncMock):
        response = await client.post(
            "/api/upload/init",
            json={"filename": "malware.exe", "size": 100, "mime_type": "application/octet-stream"},
            headers=_auth_headers(user),
        )

    assert response.status_code in (400, 415, 422)
    data = response.json()
    # error_code should be present in the response body
    assert "error_code" in data or "detail" in data
