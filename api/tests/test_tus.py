import base64
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security.security import create_access_token
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
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_storage():
    with (
        patch("app.routers.tus.create_multipart_upload", new_callable=AsyncMock) as m_create,
        patch("app.routers.tus.upload_part", new_callable=AsyncMock) as m_upload,
        patch("app.routers.tus.complete_multipart_verified", new_callable=AsyncMock) as m_complete,
        patch("app.routers.tus.abort_multipart_upload", new_callable=AsyncMock) as m_abort,
    ):
        m_create.return_value = "mock_s3_upload_id"
        m_upload.return_value = "mock_etag"
        yield {
            "create": m_create,
            "upload": m_upload,
            "complete": m_complete,
            "abort": m_abort,
        }


@pytest.mark.asyncio
async def test_tus_options(client: AsyncClient):
    response = await client.options("/api/upload/tus")
    assert response.status_code == 204
    assert response.headers["Tus-Resumable"] == "1.0.0"
    assert response.headers["Tus-Version"] == "1.0.0"
    assert "Tus-Max-Size" in response.headers
    assert response.headers["Tus-Extension"] == "creation,termination,checksum"


@pytest.mark.asyncio
async def test_tus_create_success(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup, mock_storage
):
    user = await _create_user(db_session)
    await db_session.commit()

    filename = "test.pdf"
    metadata = f"filename {base64.b64encode(filename.encode()).decode()}, filetype {base64.b64encode(b'application/pdf').decode()}"

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Upload-Length": "10485760",  # 10MB
            "Upload-Metadata": metadata,
        }
    )

    response = await client.post("/api/upload/tus", headers=headers)
    assert response.status_code == 201
    assert "Location" in response.headers
    assert response.headers["Tus-Resumable"] == "1.0.0"

    tus_id = response.headers["Location"].split("/")[-1]

    # Check redis state
    state = fake_redis_setup.data[f"tus:state:{tus_id}"]
    assert state[b"user_id"].decode() == str(user.id)
    assert state[b"offset"].decode() == "0"
    assert state[b"length"].decode() == "10485760"

    mock_storage["create"].assert_called_once()


@pytest.mark.asyncio
async def test_tus_create_missing_resumable(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()

    headers = _auth_headers(user)
    headers.update({"Upload-Length": "1000"})

    response = await client.post("/api/upload/tus", headers=headers)
    assert response.status_code == 400
    assert "Tus-Resumable header must be 1.0.0" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_create_invalid_length(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()

    headers = _auth_headers(user)
    headers.update({"Tus-Resumable": "1.0.0", "Upload-Length": "not-an-int"})

    response = await client.post("/api/upload/tus", headers=headers)
    assert response.status_code == 400
    assert "Upload-Length header must be an integer" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_create_too_large(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()

    headers = _auth_headers(user)
    headers.update(
        {"Tus-Resumable": "1.0.0", "Upload-Length": str(settings.tus_max_size_bytes + 1)}
    )

    response = await client.post("/api/upload/tus", headers=headers)
    assert response.status_code == 400
    assert "exceeds server maximum" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_head_success(client: AsyncClient, db_session: AsyncSession, fake_redis_setup):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    state = {"user_id": str(user.id), "offset": 500, "length": 1000}
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update({"Tus-Resumable": "1.0.0"})
    response = await client.head(f"/api/upload/tus/{tus_id}", headers=headers)
    assert response.status_code == 200
    assert response.headers["Upload-Offset"] == "500"
    assert response.headers["Upload-Length"] == "1000"


@pytest.mark.asyncio
async def test_tus_head_not_found(client: AsyncClient, db_session: AsyncSession, fake_redis_setup):
    user = await _create_user(db_session)
    await db_session.commit()

    headers = _auth_headers(user)
    headers.update({"Tus-Resumable": "1.0.0"})
    # Use a valid UUID that doesn't exist in the database
    missing_uuid = uuid.uuid4()
    response = await client.head(f"/api/upload/tus/{missing_uuid}", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_tus_patch_success_partial(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup, mock_storage
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    state = {
        "user_id": str(user.id),
        "upload_id": "test-upload-id",
        "quarantine_key": "q-key",
        "s3_upload_id": "s3-id",
        "filename": "test.pdf",
        "mime_type": "application/pdf",
        "offset": "0",
        "length": "20000000",
        "parts": "[]",
    }
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "0",
        }
    )

    # Create chunk smaller than length, but large enough for min chunk (5MB)
    chunk = b"a" * (5 * 1024 * 1024)
    response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=chunk)

    assert response.status_code == 204
    assert response.headers["Upload-Offset"] == str(len(chunk))
    mock_storage["upload"].assert_called_once()

    updated_state = fake_redis_setup.data[f"tus:state:{tus_id}"]
    assert updated_state[b"offset"].decode() == str(len(chunk))
    parts = json.loads(updated_state[b"parts"].decode())
    assert len(parts) == 1
    assert parts[0]["PartNumber"] == 1


@pytest.mark.asyncio
async def test_tus_patch_success_final(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup, mock_storage, mock_arq_pool
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    chunk_size = 5 * 1024 * 1024
    state = {
        "user_id": str(user.id),
        "upload_id": "test-upload-id",
        "quarantine_key": "q-key",
        "s3_upload_id": "s3-id",
        "filename": "test.pdf",
        "mime_type": "application/pdf",
        "offset": "0",
        "length": str(chunk_size),
        "parts": "[]",
    }
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "0",
        }
    )

    chunk = b"b" * chunk_size
    response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=chunk)

    assert response.status_code == 204
    assert response.headers["Upload-Offset"] == str(chunk_size)
    assert response.headers["X-Lectern-File-Key"] == "q-key"

    mock_storage["upload"].assert_called_once()
    mock_storage["complete"].assert_called_once()
    mock_arq_pool.enqueue_job.assert_called_once()

    # State should be deleted
    assert f"tus:state:{tus_id}" not in fake_redis_setup.data


@pytest.mark.asyncio
async def test_tus_patch_invalid_content_type(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    headers = _auth_headers(user)
    headers.update(
        {"Tus-Resumable": "1.0.0", "Content-Type": "application/json", "Upload-Offset": "0"}
    )

    response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=b"data")
    assert response.status_code == 400
    assert "Content-Type must be application/offset+octet-stream" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_patch_invalid_offset(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "not-an-int",
        }
    )

    response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=b"data")
    assert response.status_code == 400
    assert "Upload-Offset header must be an integer" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_patch_offset_mismatch(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    state = {"user_id": str(user.id), "offset": "500", "length": "1000", "parts": "[]"}
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "0",
        }
    )

    response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=b"data")
    assert response.status_code == 409
    assert "Upload-Offset mismatch" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_patch_chunk_too_small(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    state = {"user_id": str(user.id), "offset": "0", "length": "20000000", "parts": "[]"}
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "0",
        }
    )

    # Less than 5MB
    chunk = b"a" * 1024
    response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=chunk)
    assert response.status_code == 400
    assert "Non-final chunk too small" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_patch_chunk_too_large(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    state = {"user_id": str(user.id), "offset": "0", "length": "100000000", "parts": "[]"}
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "0",
        }
    )

    with (
        patch("app.routers.tus.settings.tus_chunk_min_bytes", 512),
        patch("app.routers.tus.settings.tus_chunk_max_bytes", 1024),
    ):
        chunk = b"a" * 2048
        response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=chunk)
        assert response.status_code == 400
        assert "Chunk too large" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_patch_empty_chunk(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    state = {"user_id": str(user.id), "offset": "500", "length": "1000", "parts": "[]"}
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "500",
        }
    )

    response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=b"")
    assert response.status_code == 204
    assert response.headers["Upload-Offset"] == "500"


@pytest.mark.asyncio
async def test_tus_delete_success(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup, mock_storage
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    state = {"user_id": str(user.id), "quarantine_key": "q-key", "s3_upload_id": "s3-id"}
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update({"Tus-Resumable": "1.0.0"})
    response = await client.delete(f"/api/upload/tus/{tus_id}", headers=headers)
    assert response.status_code == 204

    mock_storage["abort"].assert_called_once_with("q-key", "s3-id")
    assert f"tus:state:{tus_id}" not in fake_redis_setup.data


@pytest.mark.asyncio
async def test_tus_delete_not_found(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    user = await _create_user(db_session)
    await db_session.commit()

    headers = _auth_headers(user)
    headers.update({"Tus-Resumable": "1.0.0"})
    # Use a valid UUID that doesn't exist
    missing_uuid = uuid.uuid4()
    response = await client.delete(f"/api/upload/tus/{missing_uuid}", headers=headers)
    assert response.status_code == 404
    assert "Upload not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_tus_create_missing_metadata(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()

    headers = _auth_headers(user)
    headers.update({"Tus-Resumable": "1.0.0", "Upload-Length": "1000"})

    response = await client.post("/api/upload/tus", headers=headers)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_tus_create_invalid_metadata(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Upload-Length": "1000",
            "Upload-Metadata": "invalid_metadata_format_without_comma_or_space",
        }
    )

    response = await client.post("/api/upload/tus", headers=headers)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_tus_patch_compatible_mime_success(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup, mock_storage, mock_arq_pool
):
    user = await _create_user(db_session)
    await db_session.commit()

    tus_id = str(uuid.uuid4())
    # Content starting with LaTeX documentclass (will sniff as text/x-tex)
    chunk = b"\\documentclass{article}\nhello world"
    state = {
        "user_id": str(user.id),
        "upload_id": "test-upload-id",
        "quarantine_key": "q-key",
        "s3_upload_id": "s3-id",
        "filename": "test.tex",
        "mime_type": "application/x-tex",  # Declared MIME
        "offset": "0",
        "length": str(len(chunk)),
        "parts": "[]",
    }
    await fake_redis_setup.hset(f"tus:state:{tus_id}", state)

    headers = _auth_headers(user)
    headers.update(
        {
            "Tus-Resumable": "1.0.0",
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": "0",
        }
    )

    response = await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=chunk)

    # Should succeed (204) because text/x-tex and application/x-tex are compatible for .tex
    assert response.status_code == 204, (
        f"Failed: {response.status_code} - {response.json() if response.status_code != 204 else ''}"
    )
