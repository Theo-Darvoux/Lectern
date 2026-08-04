import base64
import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.redis import RedisSemaphoreTimeoutError
from app.core.storage.multipart_completion import MultipartCompletionError
from app.routers.upload.helpers import _UPLOAD_INTENT_PREFIX
from tests.test_tus import _auth_headers, _create_user


@pytest.mark.asyncio
async def test_presigned_init_binds_every_part_length(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    total = 20 * 1024 * 1024

    with (
        patch("app.routers.upload.presigned.settings.enable_presigned_multipart", True),
        patch(
            "app.routers.upload.presigned.create_multipart_upload",
            new_callable=AsyncMock,
            return_value="s3-upload",
        ),
        patch(
            "app.routers.upload.presigned.generate_presigned_upload_part",
            new_callable=AsyncMock,
            return_value="http://signed-part",
        ) as generate,
    ):
        response = await client.post(
            "/api/upload/presigned-multipart/init",
            headers=_auth_headers(user),
            json={
                "filename": "large.pdf",
                "size": total,
                "mime_type": "application/pdf",
            },
        )

    assert response.status_code == 200
    assert [part["size"] for part in response.json()["parts"]] == [
        8 * 1024 * 1024,
        8 * 1024 * 1024,
        4 * 1024 * 1024,
    ]
    assert [call.kwargs["content_length"] for call in generate.await_args_list] == [
        8 * 1024 * 1024,
        8 * 1024 * 1024,
        4 * 1024 * 1024,
    ]


@pytest.mark.asyncio
async def test_presigned_ambiguous_completion_retains_retry_manifest(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    intent_key = f"{_UPLOAD_INTENT_PREFIX}{upload_id}"
    intent = {
        "user_id": str(user.id),
        "upload_id": upload_id,
        "quarantine_key": f"quarantine/{user.id}/{upload_id}/large.pdf",
        "s3_multipart_id": "s3-upload",
        "filename": "large.pdf",
        "mime_type": "application/pdf",
        "size": 8 * 1024 * 1024,
        "part_size": 8 * 1024 * 1024,
        "num_parts": 1,
    }
    await fake_redis_setup.set(intent_key, json.dumps(intent))

    with patch(
        "app.routers.upload.presigned.complete_multipart_verified",
        new_callable=AsyncMock,
        side_effect=MultipartCompletionError("uncertain", retryable=True),
    ):
        response = await client.post(
            "/api/upload/presigned-multipart/complete",
            headers=_auth_headers(user),
            json={
                "upload_id": upload_id,
                "parts": [{"PartNumber": 1, "ETag": '"etag"'}],
            },
        )

    assert response.status_code == 503
    retained_raw = await fake_redis_setup.get(intent_key)
    assert retained_raw is not None
    retained = json.loads(retained_raw)
    assert retained["finalizing"] is True
    assert retained["part_manifest"] == [{"PartNumber": 1, "ETag": '"etag"'}]


@pytest.mark.asyncio
async def test_tus_uses_resolved_mime_for_creation_limit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    metadata = ",".join(
        (
            f"filename {base64.b64encode(b'large.svg').decode()}",
            f"filetype {base64.b64encode(b'application/octet-stream').decode()}",
        )
    )

    with patch(
        "app.routers.tus.create_multipart_upload",
        new_callable=AsyncMock,
    ) as create:
        response = await client.post(
            "/api/upload/tus",
            headers={
                **_auth_headers(user),
                "Tus-Resumable": "1.0.0",
                "Upload-Length": str(6 * 1024 * 1024),
                "Upload-Metadata": metadata,
            },
        )

    assert response.status_code == 400
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_tus_upload_part_receives_seekable_file_not_bytes(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    tus_id = str(uuid.uuid4())
    payload = b"streamed chunk"
    await fake_redis_setup.hset(
        f"tus:state:{tus_id}",
        mapping={
            "user_id": str(user.id),
            "upload_id": "upload-id",
            "quarantine_key": "quarantine/key",
            "s3_upload_id": "s3-upload",
            "filename": "file.txt",
            "mime_type": "text/plain",
            "offset": "0",
            "length": str(len(payload)),
            "parts": "[]",
        },
    )
    captured: dict[str, Any] = {}

    async def capture_part(
        _key: str,
        _upload_id: str,
        _part_number: int,
        body: Any,
    ) -> str:
        captured["is_bytes"] = isinstance(body, bytes)
        captured["seekable"] = body.seekable()
        captured["payload"] = body.read()
        return '"etag"'

    with (
        patch("app.routers.tus.upload_part", side_effect=capture_part),
        patch("app.routers.tus.complete_multipart_verified", new_callable=AsyncMock),
        patch("app.routers.tus._enqueue_processing", new_callable=AsyncMock),
    ):
        response = await client.patch(
            f"/api/upload/tus/{tus_id}",
            headers={
                **_auth_headers(user),
                "Tus-Resumable": "1.0.0",
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": "0",
            },
            content=payload,
        )

    assert response.status_code == 204
    assert captured == {
        "is_bytes": False,
        "seekable": True,
        "payload": payload,
    }


@pytest.mark.asyncio
async def test_tus_zero_byte_patch_recovers_persisted_final_manifest(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    tus_id = str(uuid.uuid4())
    length = 5 * 1024 * 1024
    await fake_redis_setup.hset(
        f"tus:state:{tus_id}",
        mapping={
            "user_id": str(user.id),
            "upload_id": "upload-id",
            "quarantine_key": "quarantine/key",
            "s3_upload_id": "s3-upload",
            "filename": "file.pdf",
            "mime_type": "application/pdf",
            "offset": str(length),
            "length": str(length),
            "parts": json.dumps([{"PartNumber": 1, "ETag": '"etag"'}]),
            "finalizing": "1",
        },
    )

    with (
        patch(
            "app.routers.tus.complete_multipart_verified",
            new_callable=AsyncMock,
        ) as complete,
        patch("app.routers.tus._enqueue_processing", new_callable=AsyncMock) as enqueue,
    ):
        response = await client.patch(
            f"/api/upload/tus/{tus_id}",
            headers={
                **_auth_headers(user),
                "Tus-Resumable": "1.0.0",
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": str(length),
            },
            content=b"",
        )

    assert response.status_code == 204
    complete.assert_awaited_once()
    enqueue.assert_awaited_once()
    retained = await fake_redis_setup.hgetall(f"tus:state:{tus_id}")
    assert retained[b"enqueued"] == b"1"


@pytest.mark.asyncio
async def test_tus_ambiguous_completion_keeps_state_for_retry(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    tus_id = str(uuid.uuid4())
    length = 5 * 1024 * 1024
    state_key = f"tus:state:{tus_id}"
    await fake_redis_setup.hset(
        state_key,
        mapping={
            "user_id": str(user.id),
            "upload_id": "upload-id",
            "quarantine_key": "quarantine/key",
            "s3_upload_id": "s3-upload",
            "filename": "file.pdf",
            "mime_type": "application/pdf",
            "offset": str(length),
            "length": str(length),
            "parts": json.dumps([{"PartNumber": 1, "ETag": '"etag"'}]),
            "finalizing": "1",
        },
    )

    with (
        patch(
            "app.routers.tus.complete_multipart_verified",
            new_callable=AsyncMock,
            side_effect=MultipartCompletionError("uncertain", retryable=True),
        ),
        patch("app.routers.tus._enqueue_processing", new_callable=AsyncMock) as enqueue,
    ):
        response = await client.patch(
            f"/api/upload/tus/{tus_id}",
            headers={
                **_auth_headers(user),
                "Tus-Resumable": "1.0.0",
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": str(length),
            },
            content=b"",
        )

    assert response.status_code == 503
    assert await fake_redis_setup.hgetall(state_key)
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_tus_global_concurrency_limit_is_enforced(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    tus_id = uuid.uuid4()

    with (
        patch(
            "app.routers.tus._load_state",
            new_callable=AsyncMock,
            return_value={
                "user_id": str(user.id),
                "upload_id": "upload-123",
                "offset": "0",
            },
        ),
        patch(
            "app.routers.tus.redis_semaphore",
            side_effect=RedisSemaphoreTimeoutError("full"),
        ),
    ):
        response = await client.patch(
            f"/api/upload/tus/{tus_id}",
            headers={
                **_auth_headers(user),
                "Tus-Resumable": "1.0.0",
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": "0",
                "Content-Length": "0",
            },
            content=b"",
        )

    assert response.status_code == 429
    assert response.headers["X-Lectern-Error"] == "ERR_TUS_CONCURRENCY_LIMIT"


@pytest.mark.asyncio
async def test_presigned_manifest_cannot_change_during_retry(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    intent_key = f"{_UPLOAD_INTENT_PREFIX}{upload_id}"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": f"quarantine/{user.id}/{upload_id}/large.pdf",
                "s3_multipart_id": "s3-upload",
                "filename": "large.pdf",
                "mime_type": "application/pdf",
                "size": 8 * 1024 * 1024,
                "part_size": 8 * 1024 * 1024,
                "num_parts": 1,
                "finalizing": True,
                "part_manifest": [{"PartNumber": 1, "ETag": '"first"'}],
            }
        ),
    )

    with patch(
        "app.routers.upload.presigned.complete_multipart_verified",
        new_callable=AsyncMock,
    ) as complete:
        response = await client.post(
            "/api/upload/presigned-multipart/complete",
            headers=_auth_headers(user),
            json={
                "upload_id": upload_id,
                "parts": [{"PartNumber": 1, "ETag": '"changed"'}],
            },
        )

    assert response.status_code == 400
    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_presigned_enqueue_failure_retains_verified_completion(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    intent_key = f"{_UPLOAD_INTENT_PREFIX}{upload_id}"
    quarantine_key = f"quarantine/{user.id}/{upload_id}/large.pdf"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "s3_multipart_id": "s3-upload",
                "filename": "large.pdf",
                "mime_type": "application/pdf",
                "size": 8 * 1024 * 1024,
                "part_size": 8 * 1024 * 1024,
                "num_parts": 1,
            }
        ),
    )

    with (
        patch(
            "app.routers.upload.presigned.complete_multipart_verified",
            new_callable=AsyncMock,
        ),
        patch(
            "app.core.storage.facade.read_object_bytes",
            new_callable=AsyncMock,
            return_value=b"%PDF-1.4",
        ),
        patch(
            "app.routers.upload.presigned.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": 8 * 1024 * 1024, "content_type": "application/pdf"},
        ),
        patch(
            "app.routers.upload.presigned._enqueue_processing",
            new_callable=AsyncMock,
            side_effect=RuntimeError("queue unavailable"),
        ),
        pytest.raises(RuntimeError, match="queue unavailable"),
    ):
        await client.post(
            "/api/upload/presigned-multipart/complete",
            headers=_auth_headers(user),
            json={
                "upload_id": upload_id,
                "parts": [{"PartNumber": 1, "ETag": '"etag"'}],
            },
        )

    retained = json.loads(await fake_redis_setup.get(intent_key))
    assert retained["multipart_completed"] is True
    assert retained["part_manifest"] == [{"PartNumber": 1, "ETag": '"etag"'}]


@pytest.mark.asyncio
async def test_tus_enqueue_failure_is_retryable_without_recompleting(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    tus_id = str(uuid.uuid4())
    length = 5 * 1024 * 1024
    state_key = f"tus:state:{tus_id}"
    await fake_redis_setup.hset(
        state_key,
        mapping={
            "user_id": str(user.id),
            "upload_id": "upload-id",
            "quarantine_key": "quarantine/key",
            "s3_upload_id": "s3-upload",
            "filename": "file.pdf",
            "mime_type": "application/pdf",
            "offset": str(length),
            "length": str(length),
            "parts": json.dumps([{"PartNumber": 1, "ETag": '"etag"'}]),
            "finalizing": "1",
        },
    )
    headers = {
        **_auth_headers(user),
        "Tus-Resumable": "1.0.0",
        "Content-Type": "application/offset+octet-stream",
        "Upload-Offset": str(length),
    }

    complete = AsyncMock()
    with (
        patch("app.routers.tus.complete_multipart_verified", complete),
        patch(
            "app.routers.tus._enqueue_processing",
            new_callable=AsyncMock,
            side_effect=RuntimeError("queue unavailable"),
        ),
        pytest.raises(RuntimeError, match="queue unavailable"),
    ):
        await client.patch(f"/api/upload/tus/{tus_id}", headers=headers, content=b"")

    retained = await fake_redis_setup.hgetall(state_key)
    assert retained[b"multipart_completed"] == b"1"
    complete.assert_awaited_once()

    with (
        patch("app.routers.tus.complete_multipart_verified", complete),
        patch("app.routers.tus._enqueue_processing", new_callable=AsyncMock) as enqueue,
    ):
        response = await client.patch(
            f"/api/upload/tus/{tus_id}",
            headers=headers,
            content=b"",
        )

    assert response.status_code == 204
    assert complete.await_count == 1
    enqueue.assert_awaited_once()
    retained = await fake_redis_setup.hgetall(state_key)
    assert retained[b"enqueued"] == b"1"


def test_redis_preserves_upload_state_instead_of_eviction() -> None:
    redis_config = (
        Path(__file__).parents[2]
        / "infra"
        / "docker"
        / "redis"
        / "redis.conf"
    ).read_text(encoding="utf-8")
    assert "appendonly yes" in redis_config
    assert "appendfsync everysec" in redis_config
    assert "maxmemory-policy noeviction" in redis_config


def test_production_seaweedfs_policy_is_rack_aware_and_immutable() -> None:
    repo_root = Path(__file__).parents[2]
    compose = (repo_root / "compose.yaml").read_text(encoding="utf-8")
    prod = (repo_root / "compose.prod.yaml").read_text(encoding="utf-8")

    assert "-defaultReplication=010" in compose
    assert "-defaultReplicaPlacement=010" in compose
    assert "chrislusf/seaweedfs:latest" not in compose
    assert "@sha256:" in prod
    assert "SEAWEEDFS_IMAGE must be pinned" in prod


def test_live_storage_workflow_covers_multipart_callers_and_deployment() -> None:
    workflow = (
        Path(__file__).parents[2]
        / ".github"
        / "workflows"
        / "seaweedfs-integration.yml"
    ).read_text(encoding="utf-8")

    for path in (
        "api/app/routers/tus.py",
        "api/app/routers/upload/**",
        "api/app/workers/cleanup_uploads.py",
        "api/app/workers/reconcile_multipart.py",
        "api/app/config.py",
        "compose.yaml",
        "compose.prod.yaml",
    ):
        assert path in workflow
    assert "run-seaweedfs-topology-tests.sh" in workflow
    assert "Resolve SeaweedFS to an immutable digest" in workflow


@pytest.mark.asyncio
async def test_presigned_definitive_completion_failure_removes_object_and_intent(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    intent_key = f"{_UPLOAD_INTENT_PREFIX}{upload_id}"
    quarantine_key = f"quarantine/{user.id}/{upload_id}/large.pdf"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "s3_multipart_id": "s3-upload",
                "filename": "large.pdf",
                "mime_type": "application/pdf",
                "size": 8 * 1024 * 1024,
                "part_size": 8 * 1024 * 1024,
                "num_parts": 1,
            }
        ),
    )

    with (
        patch(
            "app.routers.upload.presigned.complete_multipart_verified",
            new_callable=AsyncMock,
            side_effect=MultipartCompletionError("invalid parts", retryable=False),
        ),
        patch(
            "app.routers.upload.presigned.abort_multipart_upload",
            new_callable=AsyncMock,
        ),
        patch(
            "app.routers.upload.presigned.delete_object",
            new_callable=AsyncMock,
        ) as delete,
    ):
        response = await client.post(
            "/api/upload/presigned-multipart/complete",
            headers=_auth_headers(user),
            json={
                "upload_id": upload_id,
                "parts": [{"PartNumber": 1, "ETag": '"etag"'}],
            },
        )

    assert response.status_code == 400
    delete.assert_awaited_once_with(quarantine_key)
    assert await fake_redis_setup.get(intent_key) is None


@pytest.mark.asyncio
async def test_presigned_cleanup_failure_retains_intent_for_retry(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    intent_key = f"{_UPLOAD_INTENT_PREFIX}{upload_id}"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": f"quarantine/{user.id}/{upload_id}/large.pdf",
                "s3_multipart_id": "s3-upload",
                "filename": "large.pdf",
                "mime_type": "application/pdf",
                "size": 8 * 1024 * 1024,
                "part_size": 8 * 1024 * 1024,
                "num_parts": 1,
            }
        ),
    )

    with (
        patch(
            "app.routers.upload.presigned.complete_multipart_verified",
            new_callable=AsyncMock,
            side_effect=MultipartCompletionError("invalid parts", retryable=False),
        ),
        patch(
            "app.routers.upload.presigned.abort_multipart_upload",
            new_callable=AsyncMock,
        ),
        patch(
            "app.routers.upload.presigned.delete_object",
            new_callable=AsyncMock,
            side_effect=RuntimeError("storage unavailable"),
        ),
    ):
        response = await client.post(
            "/api/upload/presigned-multipart/complete",
            headers=_auth_headers(user),
            json={
                "upload_id": upload_id,
                "parts": [{"PartNumber": 1, "ETag": '"etag"'}],
            },
        )

    assert response.status_code == 503
    assert await fake_redis_setup.get(intent_key) is not None


def test_frontend_retries_uncertain_completion_without_abort() -> None:
    source = (
        Path(__file__).parents[2] / "web" / "src" / "lib" / "upload-client.ts"
    ).read_text(encoding="utf-8")

    assert "let completionStarted = false" in source
    assert "let completionAttempts = 0" in source
    assert "const uncertainCompletion" in source
    assert "if (!uncertainCompletion)" in source
    assert "Preserve that intent so the exact manifest can be retried" in source


@pytest.mark.asyncio
async def test_tus_head_reconciles_finalizing_upload_for_client_recovery(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    tus_id = str(uuid.uuid4())
    length = 5 * 1024 * 1024
    await fake_redis_setup.hset(
        f"tus:state:{tus_id}",
        mapping={
            "user_id": str(user.id),
            "upload_id": "upload-id",
            "quarantine_key": "quarantine/key",
            "s3_upload_id": "s3-upload",
            "filename": "file.pdf",
            "mime_type": "application/pdf",
            "offset": str(length),
            "length": str(length),
            "parts": json.dumps([{"PartNumber": 1, "ETag": '"etag"'}]),
            "finalizing": "1",
        },
    )

    with (
        patch("app.routers.tus.complete_multipart_verified", new_callable=AsyncMock),
        patch("app.routers.tus._enqueue_processing", new_callable=AsyncMock) as enqueue,
    ):
        response = await client.head(
            f"/api/upload/tus/{tus_id}",
            headers={**_auth_headers(user), "Tus-Resumable": "1.0.0"},
        )

    assert response.status_code == 200
    assert response.headers["Upload-Offset"] == str(length)
    assert response.headers["X-Lectern-File-Key"] == "quarantine/key"
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_presigned_abort_retains_intent_when_object_deletion_is_unavailable(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    upload_id = str(uuid.uuid4())
    intent_key = f"{_UPLOAD_INTENT_PREFIX}{upload_id}"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": f"quarantine/{user.id}/{upload_id}/large.pdf",
                "s3_multipart_id": "s3-upload",
                "filename": "large.pdf",
                "mime_type": "application/pdf",
                "size": 8 * 1024 * 1024,
            }
        ),
    )

    with (
        patch(
            "app.routers.upload.presigned.abort_multipart_upload",
            new_callable=AsyncMock,
        ),
        patch(
            "app.routers.upload.presigned.delete_object",
            new_callable=AsyncMock,
            side_effect=RuntimeError("storage unavailable"),
        ),
    ):
        response = await client.delete(
            f"/api/upload/presigned-multipart/{upload_id}",
            headers=_auth_headers(user),
        )

    assert response.status_code == 503
    assert await fake_redis_setup.get(intent_key) is not None
