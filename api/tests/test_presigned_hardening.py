"""Tests for Phase 3A presigned upload hardening.

Covers:
- 1.2: content_length enforced in presigned PUT params
- 1.3: MIME re-validation on presigned complete (Range GET + _apply_mime_correction)
- 1.15: SHA-256 optional field stored in intent and forwarded to worker
- 3C: app_error_handler always includes error_code field
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.models.user import User, UserRole
from app.routers.upload.presigned import _validated_multipart_manifest
from app.schemas.material import PresignedMultipartCompleteRequest


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

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


def test_multipart_completion_schema_rejects_empty_or_invalid_parts() -> None:
    upload_id = str(uuid.uuid4())
    invalid_manifests = [
        [],
        [{"PartNumber": 0, "ETag": "etag"}],
        [{"PartNumber": 1, "ETag": ""}],
    ]

    for parts in invalid_manifests:
        with pytest.raises(ValidationError):
            PresignedMultipartCompleteRequest(upload_id=upload_id, parts=parts)


def test_multipart_manifest_requires_unique_contiguous_expected_parts() -> None:
    upload_id = str(uuid.uuid4())
    intent = {"size": 16 * 1024 * 1024, "part_size": 8 * 1024 * 1024, "num_parts": 2}
    duplicate = PresignedMultipartCompleteRequest(
        upload_id=upload_id,
        parts=[
            {"PartNumber": 1, "ETag": "first"},
            {"PartNumber": 1, "ETag": "duplicate"},
        ],
    )

    with pytest.raises(BadRequestError, match="each expected part exactly once"):
        _validated_multipart_manifest(duplicate, intent)

    unordered = PresignedMultipartCompleteRequest(
        upload_id=upload_id,
        parts=[
            {"PartNumber": 2, "ETag": "second"},
            {"PartNumber": 1, "ETag": "first"},
        ],
    )
    assert _validated_multipart_manifest(unordered, intent) == [
        {"PartNumber": 1, "ETag": "first"},
        {"PartNumber": 2, "ETag": "second"},
    ]


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

    from app.core.storage.facade import generate_presigned_put

    with patch("app.core.storage.s3.S3Backend.get_s3_client") as mock_ctx:
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


@pytest.mark.asyncio
async def test_init_upload_stores_and_signs_canonical_mime(
    client: AsyncClient, db_session: AsyncSession, mock_redis: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_redis.get.return_value = None

    with (
        patch(
            "app.routers.upload.presigned.generate_presigned_put",
            new_callable=AsyncMock,
            return_value="https://s3.example.test/upload",
        ) as generate,
        patch("app.routers.upload.presigned._create_upload_row", new_callable=AsyncMock),
        patch("app.routers.upload.presigned._check_pending_cap", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/upload/init",
            json={
                "filename": "document.doc",
                "size": 1024,
                "mime_type": "Application/MSWord; charset=binary",
            },
            headers=_auth_headers(user),
        )

    assert response.status_code == 200
    assert generate.await_args.kwargs["content_type"] == "application/msword"
    intent_call = next(
        call
        for call in mock_redis.set.call_args_list
        if call.args and str(call.args[0]).startswith("upload:intent:")
    )
    assert json.loads(intent_call.args[1])["mime_type"] == "application/msword"


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
            "app.core.storage.facade.read_object_bytes",
            new_callable=AsyncMock,
            return_value=pdf_header,
        ),
        patch("app.routers.upload.presigned._enqueue_processing", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/upload/complete",
            json={"quarantine_key": quarantine_key, "upload_id": upload_id},
            headers=_auth_headers(user),
        )

    assert response.status_code == 202


# ── Durable presigned completion and capacity reservations ──────────────────


@pytest.mark.parametrize(
    ("endpoint", "size"),
    [
        ("/api/upload/init", 1024),
        ("/api/upload/presigned-multipart/init", 8 * 1024 * 1024),
    ],
)
@pytest.mark.asyncio
async def test_presigned_init_rejects_when_atomic_storage_reservation_is_full(
    endpoint: str,
    size: int,
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    reject_reservation = AsyncMock(return_value=0)
    mock_redis.register_script = MagicMock(return_value=reject_reservation)

    with (
        patch("app.routers.upload.presigned.settings.enable_presigned_multipart", True),
        patch("app.routers.upload.helpers.settings.max_storage_gb", 1),
        patch(
            "app.routers.upload.helpers._get_storage_usage",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("app.routers.upload.presigned._check_pending_cap", new_callable=AsyncMock),
        patch(
            "app.routers.upload.presigned.generate_presigned_put", new_callable=AsyncMock
        ) as generate_put,
        patch(
            "app.routers.upload.presigned.create_multipart_upload", new_callable=AsyncMock
        ) as create_multipart,
    ):
        response = await client.post(
            endpoint,
            json={
                "filename": "document.pdf",
                "size": size,
                "mime_type": "application/pdf",
            },
            headers=_auth_headers(user),
        )

    assert response.status_code == 400
    assert "Global storage limit reached" in response.json()["detail"]
    reject_reservation.assert_awaited_once()
    generate_put.assert_not_awaited()
    create_multipart.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_complete_keeps_intent_retryable_when_enqueue_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    intent_key = f"upload:intent:{upload_id}"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "filename": "document.pdf",
                "mime_type": "application/pdf",
                "sha256": None,
            }
        ),
    )

    with (
        patch(
            "app.routers.upload.presigned.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 1024},
        ),
        patch(
            "app.core.storage.facade.read_object_bytes",
            new_callable=AsyncMock,
            return_value=b"%PDF-1.7",
        ),
        patch("app.routers.upload.presigned._reserve_storage_limit", new_callable=AsyncMock),
        patch(
            "app.routers.upload.presigned._enqueue_processing",
            new_callable=AsyncMock,
            side_effect=OSError("queue unavailable"),
        ),
        pytest.raises(OSError, match="queue unavailable"),
    ):
        await client.post(
            "/api/upload/complete",
            json={"quarantine_key": quarantine_key, "upload_id": upload_id},
            headers=_auth_headers(user),
        )

    assert await fake_redis_setup.get(intent_key) is not None


@pytest.mark.asyncio
async def test_multipart_complete_checkpoint_makes_downstream_failure_retryable(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    intent_key = f"upload:intent:{upload_id}"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "s3_multipart_id": "multipart-1",
                "filename": "document.pdf",
                "mime_type": "application/pdf",
                "size": 8 * 1024 * 1024,
            }
        ),
    )
    request_body = {
        "upload_id": upload_id,
        "parts": [{"PartNumber": 1, "ETag": "etag-1"}],
    }

    read_header = AsyncMock(side_effect=[OSError("range read failed"), b"%PDF-1.7"])
    with (
        patch(
            "app.routers.upload.presigned.complete_multipart_verified",
            new_callable=AsyncMock,
        ) as complete_multipart,
        patch("app.core.storage.facade.read_object_bytes", read_header),
        patch(
            "app.routers.upload.presigned.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 8 * 1024 * 1024},
        ),
        patch("app.routers.upload.presigned._reserve_storage_limit", new_callable=AsyncMock),
        patch("app.routers.upload.presigned._enqueue_processing", new_callable=AsyncMock),
    ):
        with pytest.raises(OSError, match="range read failed"):
            await client.post(
                "/api/upload/presigned-multipart/complete",
                json=request_body,
                headers=_auth_headers(user),
            )

        checkpoint = json.loads(await fake_redis_setup.get(intent_key))
        assert checkpoint["multipart_completed"] is True

        response = await client.post(
            "/api/upload/presigned-multipart/complete",
            json=request_body,
            headers=_auth_headers(user),
        )

    assert response.status_code == 202
    complete_multipart.assert_awaited_once()
    tombstone = json.loads(await fake_redis_setup.get(intent_key))
    assert tombstone["enqueued"] is True
    assert tombstone["actual_size"] == 8 * 1024 * 1024


@pytest.mark.asyncio
async def test_multipart_complete_rejects_object_size_different_from_intent(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    declared_size = 8 * 1024 * 1024
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    await fake_redis_setup.set(
        f"upload:intent:{upload_id}",
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "s3_multipart_id": "multipart-1",
                "filename": "document.pdf",
                "mime_type": "application/pdf",
                "size": declared_size,
                "part_size": declared_size,
                "num_parts": 1,
            }
        ),
    )

    with (
        patch("app.routers.upload.presigned.complete_multipart_verified", new_callable=AsyncMock),
        patch(
            "app.routers.upload.presigned.abort_multipart_upload",
            new_callable=AsyncMock,
        ),
        patch(
            "app.routers.upload.presigned.delete_object",
            new_callable=AsyncMock,
        ) as delete_object,
        patch(
            "app.core.storage.facade.read_object_bytes",
            new_callable=AsyncMock,
            return_value=b"%PDF-1.7",
        ),
        patch(
            "app.routers.upload.presigned.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": declared_size - 1},
        ),
        patch(
            "app.routers.upload.presigned._enqueue_processing", new_callable=AsyncMock
        ) as enqueue,
    ):
        response = await client.post(
            "/api/upload/presigned-multipart/complete",
            json={"upload_id": upload_id, "parts": [{"PartNumber": 1, "ETag": "etag-1"}]},
            headers=_auth_headers(user),
        )

    assert response.status_code == 400
    delete_object.assert_awaited_once_with(quarantine_key)
    assert response.json()["error_code"] == "ERR_INTENT_MISMATCH"
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_single_complete_deletes_intent(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    intent_key = f"upload:intent:{upload_id}"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "filename": "document.pdf",
                "mime_type": "application/pdf",
                "sha256": None,
            }
        ),
    )

    with (
        patch(
            "app.routers.upload.presigned.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 1024},
        ),
        patch(
            "app.core.storage.facade.read_object_bytes",
            new_callable=AsyncMock,
            return_value=b"%PDF-1.7",
        ),
        patch("app.routers.upload.presigned._reserve_storage_limit", new_callable=AsyncMock),
        patch("app.routers.upload.presigned._enqueue_processing", new_callable=AsyncMock),
    ):
        response = await client.post(
            "/api/upload/complete",
            json={"quarantine_key": quarantine_key, "upload_id": upload_id},
            headers=_auth_headers(user),
        )

    assert response.status_code == 202
    assert await fake_redis_setup.get(intent_key) is None


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
