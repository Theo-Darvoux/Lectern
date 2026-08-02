import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.upload import _QUOTA_KEY_PREFIX
from tests.test_tus import _auth_headers, _create_user


@pytest.mark.asyncio
async def test_presigned_multipart_flow(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup
):
    """Test Phase 6.2: Init -> Complete multipart flow."""
    user = await _create_user(db_session)
    await db_session.commit()

    with patch("app.routers.upload.presigned.settings.enable_presigned_multipart", True):
        # 1. Init
        init_data = {
            "filename": "large_file.pdf",
            "size": 20 * 1024 * 1024,  # 20 MiB -> 3 parts (8+8+4)
            "mime_type": "application/pdf",
        }

        with (
            patch(
                "app.routers.upload.presigned.create_multipart_upload", new_callable=AsyncMock
            ) as m_create,
            patch(
                "app.routers.upload.presigned.generate_presigned_upload_part",
                new_callable=AsyncMock,
            ) as m_gen_part,
        ):
            m_create.return_value = "s3-mp-id"
            m_gen_part.return_value = "http://presigned-part-url"

            response = await client.post(
                "/api/upload/presigned-multipart/init", json=init_data, headers=_auth_headers(user)
            )
            assert response.status_code == 200
            data = response.json()
            assert data["s3_multipart_id"] == "s3-mp-id"
            assert len(data["parts"]) == 3
            upload_id = data["upload_id"]
            data["quarantine_key"]

        # 2. Complete
        complete_data = {
            "upload_id": upload_id,
            "parts": [
                {"PartNumber": 1, "ETag": "etag1"},
                {"PartNumber": 2, "ETag": "etag2"},
                {"PartNumber": 3, "ETag": "etag3"},
            ],
        }

        with (
            patch(
                "app.routers.upload.presigned.complete_multipart_upload", new_callable=AsyncMock
            ) as m_complete,
            patch("app.core.storage.facade.read_object_bytes", new_callable=AsyncMock) as m_read,
            patch("app.routers.upload.presigned.get_object_info", new_callable=AsyncMock) as m_info,
            patch(
                "app.routers.upload.presigned._enqueue_processing", new_callable=AsyncMock
            ) as m_enqueue,
        ):
            m_read.return_value = b"%PDF-1.4"
            m_info.return_value = {"size": 20 * 1024 * 1024, "content_type": "application/pdf"}
            response = await client.post(
                "/api/upload/presigned-multipart/complete",
                json=complete_data,
                headers=_auth_headers(user),
            )
            assert response.status_code == 202
            assert m_complete.called
            assert m_enqueue.called
            # Verify quota was updated
            quota_key = f"{_QUOTA_KEY_PREFIX}{user.id}"
            assert await fake_redis_setup.zcard(quota_key) == 1


@pytest.mark.asyncio
async def test_sse_replay_logic(client: AsyncClient, db_session: AsyncSession, fake_redis_setup):
    """Test Phase 2.1: Replaying missed events using Last-Event-ID."""
    user = await _create_user(db_session)
    await db_session.commit()

    file_key = f"quarantine/{user.id}/up123/test.txt"
    event_log_key = f"upload:eventlog:{file_key}"

    # Seed event log
    events = [
        json.dumps({"file_key": file_key, "status": "processing", "stage_index": 0}),
        json.dumps({"file_key": file_key, "status": "processing", "stage_index": 1}),
        json.dumps({"file_key": file_key, "status": "processing", "stage_index": 2}),
    ]
    for e in events:
        await fake_redis_setup.rpush(event_log_key, e)

    # Request with Last-Event-ID: 1 (should get events from index 1 -> events[1] and [2])
    # Note: Redis LRANGE start index is 0-based. If Last-Event-ID is 1, we want everything AFTER the 1st event.
    # The code does: await redis.lrange(event_log_key, last_event_id, -1)
    # If last_event_id=1, it gets index 1 and 2. Correct.

    headers = _auth_headers(user)
    headers["Last-Event-ID"] = "1"

    # We use a mock for the SSE response to avoid hanging
    from app.routers.upload import upload_events

    mock_request = MagicMock(spec=Request)
    mock_request.headers = headers

    response = await upload_events(file_key, mock_request, user, fake_redis_setup, db_session)

    # Manually drive the generator to check replayed events
    gen = aiter(response.body_iterator)

    # 1st replayed event (original index 2, SSE id 2)
    import typing

    ev1 = typing.cast(dict[str, typing.Any], await anext(gen))
    assert ev1["event"] == "upload"
    assert ev1["id"] == "2"
    assert json.loads(ev1["data"])["stage_index"] == 1

    # 2nd replayed event (original index 3, SSE id 3)
    import typing

    ev2 = typing.cast(dict[str, typing.Any], await anext(gen))
    assert ev2["event"] == "upload"
    assert ev2["id"] == "3"
    assert json.loads(ev2["data"])["stage_index"] == 2


@pytest.mark.asyncio
async def test_atomic_cas_ref_counts(fake_redis_setup):
    """Test Phase 1.2: Atomic Lua scripts for ref counting."""
    from app.core.security.cas import _LUA_CAS_DECR, _LUA_CAS_INCR

    cas_key = "upload:cas:test"
    # Initial state: ref_count = 1
    await fake_redis_setup.set(cas_key, json.dumps({"ref_count": 1, "file_key": "k1"}))

    # Increment
    new_count = await fake_redis_setup.eval(
        _LUA_CAS_INCR,
        3,
        cas_key,
        "storage:total_usage_bytes",
        "cas:operation:upload-v3-increment",
    )
    assert new_count == 2
    data = json.loads(await fake_redis_setup.get(cas_key))
    assert data["ref_count"] == 2

    # Decrement
    new_count = await fake_redis_setup.eval(
        _LUA_CAS_DECR,
        2,
        cas_key,
        "cas:operation:upload-v3-decrement-1",
    )
    assert new_count == 1

    # Decrement to zero (should delete)
    new_count = await fake_redis_setup.eval(
        _LUA_CAS_DECR,
        2,
        cas_key,
        "cas:operation:upload-v3-decrement-2",
    )
    assert new_count == 0
    assert await fake_redis_setup.get(cas_key) is None


@pytest.mark.asyncio
async def test_download_file_with_hash_optimization(fake_redis_setup, tmp_path):
    """Test Phase 6.1: download_file_with_hash single-pass optimization."""
    from app.core.storage.facade import download_file_with_hash

    content = b"optimised streaming content"
    expected_hash = (
        "796906960cc00099009960bdcc09bc0096cc00099009960bdcc09bc009600099"  # Not real, mock it
    )
    expected_hash = (
        "6df600cf860df06900cf860df06900cf860df06900cf860df06900cf860df069"  # Still not real
    )
    import hashlib

    expected_hash = hashlib.sha256(content).hexdigest()

    dest = tmp_path / "downloaded.txt"

    mock_body = AsyncMock()
    mock_body.read.side_effect = [content, b""]
    mock_body.close = MagicMock()

    mock_s3 = AsyncMock()
    mock_s3.get_object.return_value = {"Body": mock_body}

    with patch("app.core.storage.s3.S3Backend.get_s3_client") as m_get_client:
        m_get_client.return_value.__aenter__.return_value = mock_s3

        sha256 = await download_file_with_hash("some-key", dest)

        assert sha256 == expected_hash
        assert dest.read_bytes() == content
