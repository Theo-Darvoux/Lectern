"""Tests for upload router — filename sanitisation, MIME reconciliation,
quota checking, X-Upload-ID validation, and rate limiting.

These are all integration-style tests that hit the real FastAPI app (backed by
an in-memory SQLite DB and mocked Redis/S3/scanner) via the shared `client`
fixture from conftest.py.
"""

import io
import uuid
from unittest.mock import ANY, AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole

# ── helpers ──────────────────────────────────────────────────────────────────


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


def _pdf_file(content: bytes = b"%PDF-1.4 test") -> dict:
    return {"file": ("document.pdf", io.BytesIO(content), "application/pdf")}


def _svg_file(content: bytes) -> dict:
    return {"file": ("image.svg", io.BytesIO(content), "image/svg+xml")}


# ── Filename sanitisation ─────────────────────────────────────────────────────


@patch("app.routers.upload.direct.get_s3_client")
async def test_filename_control_chars_stripped(
    mock_s3_cm, client: AsyncClient, db_session: AsyncSession
) -> None:
    """Control characters in filenames are stripped before upload."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    content = b"%PDF-1.4 test"
    files = {"file": ("doc\x00ument\x1f.pdf", io.BytesIO(content), "application/pdf")}
    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))

    assert resp.status_code == 202
    key = resp.json()["file_key"]
    assert "\x00" not in key
    assert "\x1f" not in key


@patch("app.routers.upload.direct.get_s3_client")
async def test_filename_special_chars_replaced(
    mock_s3_cm, client: AsyncClient, db_session: AsyncSession
) -> None:
    """Special chars (#%&<>*?$, etc.) in filenames are replaced with underscores."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    files = {"file": ("my doc#2 <final>.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")}
    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))

    assert resp.status_code == 202
    key = resp.json()["file_key"]
    assert "#" not in key
    assert "<" not in key
    assert ">" not in key


async def test_filename_empty_after_sanitisation_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Filename that reduces to empty after sanitisation is rejected with 400."""
    user = await _create_user(db_session)
    await db_session.commit()

    files = {"file": ("#<>*?.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))
    assert resp.status_code == 400


# ── Extension allowlist ───────────────────────────────────────────────────────


async def test_unknown_extension_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """Files with unrecognised extensions are rejected before any scanning."""
    user = await _create_user(db_session)
    await db_session.commit()

    files = {"file": ("payload.bat", io.BytesIO(b"@echo off"), "application/bat")}
    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))
    assert resp.status_code == 400
    assert "not supported" in resp.json()["detail"]


async def test_dll_extension_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """DLL files are rejected."""
    user = await _create_user(db_session)
    await db_session.commit()

    files = {"file": ("evil.dll", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")}
    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))
    assert resp.status_code == 400


# ── X-Upload-ID validation ────────────────────────────────────────────────────


async def test_invalid_upload_id_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """A non-UUID X-Upload-ID header is rejected with 400."""
    user = await _create_user(db_session)
    await db_session.commit()

    files = _pdf_file()
    headers = {**_auth_headers(user), "X-Upload-ID": "not-a-uuid"}
    resp = await client.post("/api/upload", files=files, headers=headers)
    assert resp.status_code == 400
    assert "X-Upload-ID" in resp.json()["detail"]


@patch("app.routers.upload.direct.get_s3_client")
async def test_valid_uuid_upload_id_accepted(
    mock_s3_cm, client: AsyncClient, db_session: AsyncSession
) -> None:
    """A valid UUID X-Upload-ID header is accepted and reflected in the file_key."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    upload_id = str(uuid.uuid4())
    headers = {**_auth_headers(user), "X-Upload-ID": upload_id}
    resp = await client.post("/api/upload", files=_pdf_file(), headers=headers)
    assert resp.status_code == 202
    assert upload_id in resp.json()["file_key"]


# ── MIME reconciliation ───────────────────────────────────────────────────────


@patch("app.routers.upload.direct.get_s3_client")
async def test_noncanonical_uuid_upload_id_is_canonicalized(
    mock_s3_cm, client: AsyncClient, db_session: AsyncSession
) -> None:
    """Valid caller UUIDs are normalized before DB/cache/storage use."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    canonical = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    supplied = canonical.upper()
    headers = {**_auth_headers(user), "X-Upload-ID": supplied}
    resp = await client.post("/api/upload", files=_pdf_file(), headers=headers)

    assert resp.status_code == 202
    assert resp.json()["upload_id"] == canonical
    assert f"/{canonical}/" in resp.json()["file_key"]
    assert supplied not in resp.json()["file_key"]


@patch("app.routers.upload.direct.get_s3_client")
async def test_mime_mismatch_rejected(
    mock_s3_cm, client: AsyncClient, db_session: AsyncSession
) -> None:
    """If magic bytes reveal a JPEG but the filename says .png, the upload is rejected."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    # A real JPEG payload but with .png extension
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 500
    files = {"file": ("photo.png", io.BytesIO(jpeg_bytes), "image/jpeg")}
    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))

    assert resp.status_code == 400
    assert "match" in resp.json()["detail"].lower()


# ── Redis quota enforcement ───────────────────────────────────────────────────


@patch("app.routers.upload.direct.get_s3_client")
async def test_quota_exceeded_rejected(
    mock_s3_cm, client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
) -> None:
    """Upload is rejected when the user's pending upload count hits the cap."""
    from app.routers.upload.helpers import MAX_PENDING_UPLOADS

    user = await _create_user(db_session)
    await db_session.commit()

    # Mock quota full
    mock_redis.zcard.return_value = MAX_PENDING_UPLOADS + 1
    mock_redis.pipeline.return_value.__aenter__.return_value.execute.return_value = [
        1,
        True,
        1,
        True,
    ]

    files = _pdf_file()
    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))
    assert resp.status_code == 400
    assert "pending uploads" in resp.json()["detail"].lower()


async def test_privileged_user_no_quota_cap(
    client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
) -> None:
    """Privileged users are never blocked by the pending-upload count cap."""
    from app.routers.upload.helpers import MAX_PENDING_UPLOADS

    user = await _create_user(db_session, UserRole.BUREAU)
    await db_session.commit()

    # Well above the student cap — must NOT be rejected
    mock_redis.zcard.return_value = MAX_PENDING_UPLOADS * 10
    mock_redis.pipeline.return_value.__aenter__.return_value.execute.side_effect = [
        [1, True, 1, True],  # rate limit pipeline
    ]

    with patch("app.routers.upload.direct.get_s3_client") as ms3:
        s3 = AsyncMock()
        ms3.return_value.__aenter__.return_value = s3

        resp = await client.post("/api/upload", files=_pdf_file(), headers=_auth_headers(user))
    assert resp.status_code == 202, (
        "Privileged user should never be blocked by pending-upload count"
    )


async def test_quota_redis_failure_is_permissive(
    client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
) -> None:
    """If Redis quota check fails, the upload proceeds (permissive fallback)."""
    user = await _create_user(db_session)
    await db_session.commit()

    # Make the quota zcard call raise
    mock_redis.zcard.side_effect = ConnectionError("Redis down")

    with patch("app.routers.upload.direct.get_s3_client") as ms3:
        s3 = AsyncMock()
        ms3.return_value.__aenter__.return_value = s3

        resp = await client.post("/api/upload", files=_pdf_file(), headers=_auth_headers(user))
    assert resp.status_code == 202


# ── SVG size guard ─────────────────────────────────────────────────────────────


async def test_svg_too_large_rejected(
    client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
) -> None:
    """SVG files over the threshold are rejected before any parsing."""
    from app.routers.upload import LARGE_SVG_THRESHOLD

    user = await _create_user(db_session)
    await db_session.commit()

    svg_content = (
        b'<svg xmlns="http://www.w3.org/2000/svg">' + b"x" * (LARGE_SVG_THRESHOLD + 1) + b"</svg>"
    )
    files = {"file": ("huge.svg", io.BytesIO(svg_content), "image/svg+xml")}

    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))

    assert resp.status_code == 400
    assert "file size exceeds" in resp.json()["detail"].lower()


# ── WAV upload ────────────────────────────────────────────────────────────────


@patch("app.routers.upload.direct.get_s3_client")
async def test_wav_upload_accepted_for_async_processing(
    mock_s3_cm, client: AsyncClient, db_session: AsyncSession, mock_arq_pool: AsyncMock
) -> None:
    """WAV upload is accepted (202) and enqueued for async processing.

    Conversion from WAV to Opus/WebM happens in the background worker,
    not in the router — the quarantine key retains the original .wav extension.
    """
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 100

    files = {"file": ("recording.wav", io.BytesIO(wav_bytes), "audio/wav")}
    resp = await client.post("/api/upload", files=files, headers=_auth_headers(user))

    assert resp.status_code == 202
    data = resp.json()
    assert data["mime_type"] == "audio/wav"
    assert data["file_key"].endswith(".wav")
    assert data["file_key"].startswith("quarantine/")
    mock_arq_pool.enqueue_job.assert_called_once()


# ── Job enqueued after upload ──────────────────────────────────────────────────


@patch("app.routers.upload.direct.get_s3_client")
async def test_processing_job_enqueued_after_upload(
    mock_s3_cm, client: AsyncClient, db_session: AsyncSession, mock_arq_pool: AsyncMock
) -> None:
    """After a successful upload, process_upload is enqueued in the ARQ pool."""
    mock_s3 = AsyncMock()
    mock_s3_cm.return_value.__aenter__.return_value = mock_s3

    user = await _create_user(db_session)
    await db_session.commit()

    resp = await client.post("/api/upload", files=_pdf_file(), headers=_auth_headers(user))
    assert resp.status_code == 202

    # V2: _queue_name is now included; test PDF goes to the slow/heavy queue
    from app.routers.upload import _SLOW_QUEUE_NAME

    mock_arq_pool.enqueue_job.assert_called_once_with(
        "process_upload",
        _queue_name=_SLOW_QUEUE_NAME,
        user_id=str(user.id),
        upload_id=ANY,
        quarantine_key=resp.json()["file_key"],
        original_filename="document.pdf",
        mime_type="application/pdf",
        expected_sha256=ANY,
        trace_context=ANY,
    )


# ── Unauthenticated request ───────────────────────────────────────────────────


async def test_upload_requires_authentication(client: AsyncClient) -> None:
    """Upload endpoint returns 401 when no Authorization header is given."""
    resp = await client.post("/api/upload", files=_pdf_file())
    assert resp.status_code == 401
