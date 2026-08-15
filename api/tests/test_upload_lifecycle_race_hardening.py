"""Regression tests for renewable admission and cancellation/finalization races."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.redis import RedisSemaphoreTimeoutError, redis_semaphore
from app.models.upload import Upload
from app.models.user import User, UserRole
from app.workers.upload.context import WorkerContext
from app.workers.upload.repository import UploadWorkerRepository


async def _create_user_and_upload(
    db: AsyncSession,
    *,
    upload_id: str,
    quarantine_key: str,
    size: int,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        display_name="Lifecycle race tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Upload(
            upload_id=upload_id,
            user_id=user.id,
            quarantine_key=quarantine_key,
            filename="document.pdf",
            mime_type="application/pdf",
            size_bytes=size,
            status="pending",
        )
    )
    await db.commit()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


def _serialized_lock() -> tuple[dict[str, asyncio.Lock], Any]:
    locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lock(_redis: Any, name: str, **_kwargs: Any) -> AsyncIterator[None]:
        async with locks.setdefault(name, asyncio.Lock()):
            yield

    return locks, lock


@pytest.mark.asyncio
async def test_renewal_keeps_semaphore_full_past_original_lease(fake_redis_setup: Any) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with redis_semaphore(
            fake_redis_setup,
            "tus:renewal-regression",
            1,
            timeout=0.2,
            retry_interval=0.005,
            expire=0.06,
        ):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await asyncio.wait_for(entered.wait(), timeout=1)
    # Wait longer than the original lease. Renewal must keep the slot occupied.
    await asyncio.sleep(0.12)

    with pytest.raises(RedisSemaphoreTimeoutError):
        async with redis_semaphore(
            fake_redis_setup,
            "tus:renewal-regression",
            1,
            timeout=0.04,
            retry_interval=0.005,
            expire=0.06,
        ):
            pytest.fail("A second holder entered while the first lease was active")

    release.set()
    await task


def test_completion_recovery_and_cancellation_use_shared_lock_names() -> None:
    import inspect

    from app.routers import tus
    from app.routers.upload import presigned

    patch_source = inspect.getsource(tus.tus_patch) + inspect.getsource(tus._tus_patch_admitted)
    head_source = inspect.getsource(tus.tus_head)
    delete_source = inspect.getsource(tus.tus_delete)
    assert "_tus_lock_name(tus_id_str)" in patch_source
    assert "_tus_lock_name(tus_id_str)" in head_source
    assert "_tus_lock_name(tus_id_str)" in delete_source

    complete_source = inspect.getsource(presigned.presigned_multipart_complete)
    abort_source = inspect.getsource(presigned.presigned_multipart_abort)
    assert "upload_lifecycle_lock_name(data.upload_id)" in complete_source
    assert "upload_lifecycle_lock_name(upload_id)" in abort_source
    assert "cancel_upload_lifecycle" in abort_source


@pytest.mark.asyncio
async def test_worker_status_transition_cannot_revive_cancelled_upload(
    db_session: AsyncSession,
) -> None:
    import app.core.database.database as database

    upload_id = str(uuid.uuid4())
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        display_name="Cancelled worker test",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db_session.add(user)
    await db_session.flush()
    row = Upload(
        upload_id=upload_id,
        user_id=user.id,
        quarantine_key=f"quarantine/{user.id}/{upload_id}/document.pdf",
        filename="document.pdf",
        mime_type="application/pdf",
        size_bytes=128,
        status="cancelled",
    )
    db_session.add(row)
    await db_session.commit()

    repo = UploadWorkerRepository(
        WorkerContext(redis=AsyncMock(), db_sessionmaker=database.async_session_factory)
    )
    updated = await repo.update_upload_status(upload_id, "processing")
    processing_updated = await repo.update_processing_status(upload_id, "running")

    assert updated is False
    assert processing_updated is False
    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_tus_head_finalization_and_delete_are_serialized_cancellation_wins(
    fake_redis_setup: Any,
) -> None:
    from types import SimpleNamespace

    from app.routers.tus import tus_delete, tus_head

    upload_id = str(uuid.uuid4())
    tus_id = str(uuid.uuid4())
    size = 5 * 1024 * 1024
    user = SimpleNamespace(id=uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    state_key = f"tus:state:{tus_id}"
    await fake_redis_setup.hset(
        state_key,
        mapping={
            "user_id": str(user.id),
            "upload_id": upload_id,
            "quarantine_key": quarantine_key,
            "s3_upload_id": "s3-upload-1",
            "filename": "document.pdf",
            "mime_type": "application/pdf",
            "offset": str(size),
            "length": str(size),
            "parts": json.dumps([{"PartNumber": 1, "ETag": "etag-1"}]),
            "finalizing": "1",
        },
    )

    _locks, shared_lock = _serialized_lock()
    enqueue_entered = asyncio.Event()
    release_enqueue = asyncio.Event()
    db = AsyncMock()
    row = type(
        "UploadRow",
        (),
        {
            "user_id": user.id,
            "quarantine_key": quarantine_key,
            "final_key": None,
            "content_sha256": None,
            "sha256": None,
            "cas_ref_count": 0,
            "status": "pending",
            "error_detail": None,
        },
    )()
    db.scalar = AsyncMock(side_effect=[row, None])

    async def blocked_enqueue(*_args: Any, **_kwargs: Any) -> None:
        enqueue_entered.set()
        await release_enqueue.wait()

    with (
        patch("app.routers.tus.redis_lock", shared_lock),
        patch("app.routers.tus._ensure_upload_active", new_callable=AsyncMock),
        patch("app.routers.tus.complete_multipart_verified", new_callable=AsyncMock),
        patch("app.routers.tus._enqueue_processing", side_effect=blocked_enqueue),
        patch("app.routers.tus.abort_multipart_upload", new_callable=AsyncMock),
        patch("app.routers.tus.delete_object", new_callable=AsyncMock) as delete_object,
        patch("app.routers.tus._release_storage_reservation", new_callable=AsyncMock),
    ):
        head_task = asyncio.create_task(tus_head(uuid.UUID(tus_id), user, fake_redis_setup, db))
        await asyncio.wait_for(enqueue_entered.wait(), timeout=2)
        delete_task = asyncio.create_task(tus_delete(uuid.UUID(tus_id), user, fake_redis_setup, db))
        await asyncio.sleep(0)
        assert not delete_task.done()
        release_enqueue.set()
        head_response, delete_response = await asyncio.gather(head_task, delete_task)

    assert head_response.status_code == 200
    assert delete_response.status_code == 204
    delete_object.assert_awaited_once_with(quarantine_key)
    assert await fake_redis_setup.hgetall(state_key) == {}
    assert await fake_redis_setup.get(f"upload:cancel:{upload_id}") is not None
    assert row.status == "cancelled"
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_presigned_completion_and_abort_are_serialized_cancellation_wins(
    fake_redis_setup: Any,
) -> None:
    from types import SimpleNamespace

    from app.routers.upload.presigned import (
        presigned_multipart_abort,
        presigned_multipart_complete,
    )
    from app.schemas.material import PresignedMultipartCompleteRequest

    upload_id = str(uuid.uuid4())
    size = 8 * 1024 * 1024
    user = SimpleNamespace(id=uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    intent_key = f"upload:intent:{upload_id}"
    intent = {
        "user_id": str(user.id),
        "upload_id": upload_id,
        "quarantine_key": quarantine_key,
        "s3_multipart_id": "s3-upload-1",
        "filename": "document.pdf",
        "mime_type": "application/pdf",
        "size": size,
        "part_size": size,
        "num_parts": 1,
        "part_manifest": [{"PartNumber": 1, "ETag": "etag-1"}],
        "multipart_completed": True,
        "actual_size": size,
    }
    await fake_redis_setup.set(intent_key, json.dumps(intent))

    _locks, shared_lock = _serialized_lock()
    enqueue_entered = asyncio.Event()
    release_enqueue = asyncio.Event()
    db = AsyncMock()
    row = type(
        "UploadRow",
        (),
        {
            "user_id": user.id,
            "quarantine_key": quarantine_key,
            "final_key": None,
            "content_sha256": None,
            "sha256": None,
            "cas_ref_count": 0,
            "status": "pending",
            "error_detail": None,
        },
    )()
    db.scalar = AsyncMock(side_effect=[row, None])
    request = PresignedMultipartCompleteRequest(
        upload_id=upload_id,
        parts=[{"PartNumber": 1, "ETag": "etag-1"}],
    )

    async def blocked_enqueue(*_args: Any, **_kwargs: Any) -> None:
        enqueue_entered.set()
        await release_enqueue.wait()

    with (
        patch("app.routers.upload.presigned.redis_lock", shared_lock),
        patch(
            "app.routers.upload.presigned._ensure_presigned_upload_active",
            new_callable=AsyncMock,
        ),
        patch(
            "app.core.storage.facade.read_object_bytes",
            new_callable=AsyncMock,
            return_value=b"%PDF-1.7",
        ),
        patch(
            "app.routers.upload.presigned.get_object_info",
            new_callable=AsyncMock,
            return_value={"size": size},
        ),
        patch(
            "app.routers.upload.presigned._reserve_storage_limit",
            new_callable=AsyncMock,
        ),
        patch("app.routers.upload.presigned._enqueue_processing", side_effect=blocked_enqueue),
        patch(
            "app.routers.upload.presigned.abort_multipart_upload",
            new_callable=AsyncMock,
        ),
        patch(
            "app.routers.upload.presigned.delete_object",
            new_callable=AsyncMock,
        ) as delete_object,
        patch(
            "app.routers.upload.presigned._release_storage_reservation",
            new_callable=AsyncMock,
        ),
    ):
        complete_task = asyncio.create_task(
            presigned_multipart_complete(request, user, fake_redis_setup, db)
        )
        await asyncio.wait_for(enqueue_entered.wait(), timeout=2)
        abort_task = asyncio.create_task(
            presigned_multipart_abort(upload_id, user, fake_redis_setup, db)
        )
        await asyncio.sleep(0)
        assert not abort_task.done()
        release_enqueue.set()
        complete_response, abort_response = await asyncio.gather(complete_task, abort_task)

    assert complete_response.status.value == "processing"
    assert abort_response is None
    delete_object.assert_awaited_once_with(quarantine_key)
    assert await fake_redis_setup.get(intent_key) is None
    assert await fake_redis_setup.get(f"upload:cancel:{upload_id}") is not None
    assert row.status == "cancelled"
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_tus_cleanup_failure_retains_cancelled_retry_state(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    upload_id = str(uuid.uuid4())
    tus_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key="placeholder",
        size=10,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    state_key = f"tus:state:{tus_id}"
    await fake_redis_setup.hset(
        state_key,
        mapping={
            "user_id": str(user.id),
            "upload_id": upload_id,
            "quarantine_key": quarantine_key,
            "s3_upload_id": "s3-upload-1",
            "filename": "document.pdf",
            "mime_type": "application/pdf",
            "offset": "10",
            "length": "10",
            "parts": "[]",
            "multipart_completed": "1",
            "enqueued": "1",
        },
    )

    with (
        patch("app.routers.tus.abort_multipart_upload", new_callable=AsyncMock),
        patch(
            "app.routers.tus.delete_object",
            new_callable=AsyncMock,
            side_effect=OSError("storage unavailable"),
        ),
    ):
        response = await client.delete(f"/api/upload/tus/{tus_id}", headers=_auth_headers(user))

    assert response.status_code == 503
    retained = await fake_redis_setup.hgetall(state_key)
    assert retained[b"cancelled"] == b"1"
    assert await fake_redis_setup.get(f"upload:cancel:{upload_id}") is not None


@pytest.mark.asyncio
async def test_presigned_cleanup_failure_retains_cancelled_retry_state(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key="placeholder",
        size=10,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    intent_key = f"upload:intent:{upload_id}"
    await fake_redis_setup.set(
        intent_key,
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "s3_multipart_id": "s3-upload-1",
                "filename": "document.pdf",
                "mime_type": "application/pdf",
                "size": 10,
                "multipart_completed": True,
                "enqueued": True,
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
            side_effect=OSError("storage unavailable"),
        ),
    ):
        response = await client.delete(
            f"/api/upload/presigned-multipart/{upload_id}",
            headers=_auth_headers(user),
        )

    assert response.status_code == 503
    retained_raw = await fake_redis_setup.get(intent_key)
    assert retained_raw is not None
    assert json.loads(retained_raw)["cancelled"] is True
    assert await fake_redis_setup.get(f"upload:cancel:{upload_id}") is not None


@pytest.mark.asyncio
async def test_shared_cancellation_releases_published_cas_exactly_once(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.core.security.cas import decrement_cas_ref as real_decrement_cas_ref
    from app.core.security.cas import hmac_cas_key
    from app.routers.upload.cancellation import cancel_upload_lifecycle

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    user_id = str(user.id)
    quarantine_key = f"quarantine/{user_id}/{upload_id}/document.pdf"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    original_sha256 = "f" * 64
    content_sha256 = "a" * 64
    row.quarantine_key = quarantine_key
    row.final_key = "cas/published-object"
    row.sha256 = original_sha256
    row.content_sha256 = content_sha256
    row.cas_ref_count = 1
    row.status = "clean"
    await db_session.commit()

    await fake_redis_setup.set(
        hmac_cas_key(content_sha256),
        json.dumps({"ref_count": 1, "final_key": row.final_key, "size": 128}),
    )
    await fake_redis_setup.set(f"upload:status:{quarantine_key}", "clean")
    await fake_redis_setup.set(f"upload:eventlog:{quarantine_key}", "clean-event")
    await fake_redis_setup.set(f"upload:sha256:{user_id}:{original_sha256}", row.final_key)

    from app.core.common.exceptions import ServiceUnavailableError

    delete_object = AsyncMock(side_effect=[OSError("storage unavailable"), None])
    release_reservation = AsyncMock()
    with patch(
        "app.routers.upload.cancellation.decrement_cas_ref",
        new=AsyncMock(wraps=real_decrement_cas_ref),
    ) as decrement:
        with pytest.raises(ServiceUnavailableError):
            await cancel_upload_lifecycle(
                upload_id=upload_id,
                user_id=user_id,
                redis=fake_redis_setup,
                db=db_session,
                reason="Cancelled by user",
                delete_object_fn=delete_object,
                release_reservation_fn=release_reservation,
            )
        await cancel_upload_lifecycle(
            upload_id=upload_id,
            user_id=user_id,
            redis=fake_redis_setup,
            db=db_session,
            reason="Cancelled by user",
            delete_object_fn=delete_object,
            release_reservation_fn=release_reservation,
        )

    assert decrement.await_count == 1
    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.cas_ref_count == 0
    assert await fake_redis_setup.get(hmac_cas_key(content_sha256)) is None
    assert await fake_redis_setup.get(f"upload:status:{quarantine_key}") is None
    assert await fake_redis_setup.get(f"upload:eventlog:{quarantine_key}") is None
    assert await fake_redis_setup.get(f"upload:sha256:{user_id}:{original_sha256}") is None


@pytest.mark.asyncio
async def test_worker_cancellation_after_publish_blocks_clean_and_post_scan() -> None:
    from pathlib import Path
    from types import SimpleNamespace

    from app.schemas.material import UploadStatus
    from app.workers.upload.exceptions import UploadError
    from app.workers.upload.pipeline import UploadPipeline

    redis = AsyncMock()
    ctx = WorkerContext(redis=redis, db_sessionmaker=AsyncMock(), job_try=1)
    pipeline = UploadPipeline(
        ctx,
        user_id=str(uuid.uuid4()),
        upload_id=str(uuid.uuid4()),
        quarantine_key="quarantine/user/upload/document.pdf",
        original_filename="document.pdf",
        mime_type="application/pdf",
        expected_sha256=None,
    )
    pipeline.pf = SimpleNamespace()
    pipeline.tmp_path = Path("/tmp/not-used")
    pipeline.original_sha256 = "b" * 64
    pipeline.cas_key = "upload:cas:key"
    pipeline.initial_size = 128
    pipeline.repo.publish_clean_upload = AsyncMock(return_value=True)
    pipeline.repo.update_processing_status = AsyncMock()
    pipeline.cache.emit_event = AsyncMock()
    pipeline.cache.is_cancelled = AsyncMock(side_effect=[False, True])
    pipeline._cancel_current_upload = AsyncMock()  # type: ignore[method-assign]

    final_result = SimpleNamespace(
        final_key="cas/published",
        safe_name="document.pdf",
        final_size=128,
        content_sha256="c" * 64,
        db_cas_key="upload:cas:key",
        new_cas_ref=1,
    )

    @asynccontextmanager
    async def no_op_lock(*_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
        yield

    arq_pool = AsyncMock()
    with (
        patch("app.workers.upload.pipeline.redis_lock", no_op_lock),
        patch(
            "app.workers.upload.pipeline.run_finalize_storage",
            new=AsyncMock(return_value=final_result),
        ),
        patch.object(pipeline, "_check_bazaar_before_finalize", new=AsyncMock()),
        patch("app.core.database.redis.arq_pool", arq_pool),
        patch("app.config.settings.bazaar_async_enabled", False),
    ):
        with pytest.raises(UploadError, match="cancelled"):
            await pipeline._fast_finalize_and_enqueue_post_scan()

    emitted_payloads = [call.args[3] for call in pipeline.cache.emit_event.await_args_list]
    assert not any(f'"status": "{UploadStatus.CLEAN}"' in payload for payload in emitted_payloads)
    arq_pool.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_tus_database_state_is_terminal() -> None:
    from app.routers.tus import _upload_is_cancelled

    db = AsyncMock()
    db.scalar = AsyncMock(return_value="failed")
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    state = {"upload_id": str(uuid.uuid4())}

    assert await _upload_is_cancelled(state, redis, db) is True


def test_tus_wrong_offset_is_checked_before_body_admission() -> None:
    import inspect

    from app.routers import tus

    source = inspect.getsource(tus.tus_patch)
    assert source.index("client_offset != preflight_offset") < source.index("redis_semaphore(")


@pytest.mark.asyncio
async def test_tus_delete_releases_cas_published_before_tombstone_expiry(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.core.security.cas import hmac_cas_key

    upload_id = str(uuid.uuid4())
    tus_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key="placeholder",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    content_sha256 = "d" * 64
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.final_key = "cas/published-tus"
    row.content_sha256 = content_sha256
    row.cas_ref_count = 1
    row.status = "clean"
    await db_session.commit()

    await fake_redis_setup.set(
        hmac_cas_key(content_sha256),
        json.dumps({"ref_count": 1, "final_key": row.final_key, "size": 128}),
    )
    await fake_redis_setup.hset(
        f"tus:state:{tus_id}",
        mapping={
            "user_id": str(user.id),
            "upload_id": upload_id,
            "quarantine_key": quarantine_key,
            "s3_upload_id": "s3-upload",
            "filename": "document.pdf",
            "mime_type": "application/pdf",
            "offset": "128",
            "length": "128",
            "parts": "[]",
            "multipart_completed": "1",
            "enqueued": "1",
        },
    )

    with (
        patch("app.routers.tus.abort_multipart_upload", new_callable=AsyncMock),
        patch("app.routers.tus.delete_object", new_callable=AsyncMock),
    ):
        response = await client.delete(
            f"/api/upload/tus/{tus_id}",
            headers=_auth_headers(user),
        )

    assert response.status_code == 204
    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.cas_ref_count == 0
    assert await fake_redis_setup.get(hmac_cas_key(content_sha256)) is None


@pytest.mark.asyncio
async def test_presigned_abort_releases_cas_published_before_tombstone_expiry(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.core.security.cas import hmac_cas_key

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key="placeholder",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    content_sha256 = "e" * 64
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.final_key = "cas/published-presigned"
    row.content_sha256 = content_sha256
    row.cas_ref_count = 1
    row.status = "clean"
    await db_session.commit()

    await fake_redis_setup.set(
        hmac_cas_key(content_sha256),
        json.dumps({"ref_count": 1, "final_key": row.final_key, "size": 128}),
    )
    await fake_redis_setup.set(
        f"upload:intent:{upload_id}",
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "s3_multipart_id": "s3-upload",
                "filename": "document.pdf",
                "mime_type": "application/pdf",
                "size": 128,
                "multipart_completed": True,
                "enqueued": True,
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
        ),
    ):
        response = await client.delete(
            f"/api/upload/presigned-multipart/{upload_id}",
            headers=_auth_headers(user),
        )

    assert response.status_code == 204
    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.cas_ref_count == 0
    assert await fake_redis_setup.get(hmac_cas_key(content_sha256)) is None


@pytest.mark.asyncio
async def test_worker_database_cancellation_is_authoritative_without_redis_marker(
    db_session: AsyncSession,
) -> None:
    import app.core.database.database as database
    from app.workers.upload.exceptions import UploadError
    from app.workers.upload.pipeline import UploadPipeline

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.status = "cancelled"
    await db_session.commit()

    redis = AsyncMock()
    pipeline = UploadPipeline(
        WorkerContext(redis=redis, db_sessionmaker=database.async_session_factory),
        user_id=str(user.id),
        upload_id=upload_id,
        quarantine_key=row.quarantine_key,
        original_filename=row.filename,
        mime_type=row.mime_type,
        expected_sha256=None,
    )
    pipeline.cache.is_cancelled = AsyncMock(return_value=False)
    pipeline._cancel_current_upload = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(UploadError, match="cancelled"):
        await pipeline._check_cancellation("after clean publication")

    pipeline._cancel_current_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_scan_database_cancellation_without_marker_blocks_republication(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from types import SimpleNamespace

    import app.core.database.database as database
    from app.workers.process_upload_post_scan import process_upload_post_scan

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.final_key = "cas/post-scan-cancelled"
    row.sha256 = "1" * 64
    row.content_sha256 = "2" * 64
    row.cas_ref_count = 1
    row.status = "clean"
    row.processing_status = "pending"
    await db_session.commit()

    pf = MagicMock()
    pf.path = Path("/tmp/post-scan-cancelled.pdf")
    pf.cleanup = MagicMock()
    download_result = SimpleNamespace(pf=pf, actual_mime="application/pdf")

    @asynccontextmanager
    async def cancel_before_publish(*_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
        async with database.async_session_factory() as session:
            locked = await session.scalar(
                select(Upload).where(Upload.upload_id == upload_id).with_for_update()
            )
            assert locked is not None
            locked.status = "cancelled"
            await session.commit()
        yield

    emit_event = AsyncMock()
    webhook = AsyncMock()
    auto_merge = AsyncMock()
    delete_object = AsyncMock()

    with (
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            new=AsyncMock(return_value=download_result),
        ),
        patch(
            "app.workers.process_upload_post_scan.run_strip_only",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage",
            new=AsyncMock(return_value="/tmp/post-scan-thumb.webp"),
        ),
        patch(
            "app.workers.process_upload_post_scan.upload_file_multipart",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan._post_scan_lifecycle_guard",
            new=cancel_before_publish,
        ),
        patch(
            "app.workers.process_upload_post_scan.UploadCacheRepository.emit_event",
            new=emit_event,
        ),
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.get_auth_config",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.maybe_dispatch_webhook",
            new=webhook,
        ),
        patch(
            "app.workers.process_upload_post_scan._trigger_pending_auto_merges",
            new=auto_merge,
        ),
        patch(
            "app.workers.process_upload_post_scan.delete_object",
            new=delete_object,
        ),
    ):
        await process_upload_post_scan(
            {
                "redis": fake_redis_setup,
                "db_sessionmaker": database.async_session_factory,
                "job_try": 1,
            },
            upload_id=upload_id,
            user_id=str(user.id),
            quarantine_key=quarantine_key,
            original_filename="document.pdf",
            mime_type="application/pdf",
            original_sha256="1" * 64,
            cas_key="upload:cas:post-scan-cancelled",
            cas_s3_key=row.final_key,
            initial_size=128,
        )

    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "cancelled"
    emit_event.assert_not_awaited()
    webhook.assert_not_awaited()
    auto_merge.assert_not_awaited()
    delete_object.assert_awaited_once_with(f"thumbnails/post-scan-cancelled/{upload_id}.webp")


@pytest.mark.asyncio
async def test_post_scan_holds_lifecycle_lock_through_clean_event_and_followups(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from types import SimpleNamespace

    import app.core.database.database as database
    from app.core.security.cas import hmac_cas_key
    from app.routers.upload.cancellation import (
        cancel_upload_lifecycle,
        upload_lifecycle_lock_name,
    )
    from app.workers.process_upload_post_scan import process_upload_post_scan
    from app.workers.upload.cache_repo import UploadCacheRepository

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    original_sha256 = "3" * 64
    content_sha256 = "4" * 64
    row.quarantine_key = quarantine_key
    row.final_key = "cas/post-scan-serialized"
    row.sha256 = original_sha256
    row.content_sha256 = content_sha256
    row.cas_ref_count = 1
    row.status = "clean"
    row.processing_status = "pending"
    await db_session.commit()

    await fake_redis_setup.set(
        hmac_cas_key(content_sha256),
        json.dumps({"ref_count": 1, "final_key": row.final_key, "size": 128}),
    )
    status_key = f"upload:status:{quarantine_key}"
    await fake_redis_setup.set(
        status_key,
        json.dumps(
            {
                "upload_id": upload_id,
                "file_key": quarantine_key,
                "status": "clean",
                "result": {
                    "file_key": row.final_key,
                    "size": 128,
                    "original_size": 128,
                    "mime_type": "application/pdf",
                },
            }
        ),
    )

    pf = MagicMock()
    pf.path = Path("/tmp/post-scan-serialized.pdf")
    pf.cleanup = MagicMock()
    download_result = SimpleNamespace(pf=pf, actual_mime="application/pdf")

    _locks, shared_lock = _serialized_lock()
    emit_entered = asyncio.Event()
    release_emit = asyncio.Event()
    original_emit = UploadCacheRepository.emit_event

    async def blocked_emit(
        cache: UploadCacheRepository,
        event_status_key: str,
        event_channel: str,
        event_log_key: str,
        payload_json: str,
    ) -> None:
        emit_entered.set()
        await release_emit.wait()
        await original_emit(
            cache,
            event_status_key,
            event_channel,
            event_log_key,
            payload_json,
        )

    webhook = AsyncMock()
    auto_merge = AsyncMock()
    delete_object = AsyncMock()
    release_reservation = AsyncMock()

    async def cancel() -> None:
        async with shared_lock(
            fake_redis_setup,
            upload_lifecycle_lock_name(upload_id),
        ):
            async with database.async_session_factory() as session:
                await cancel_upload_lifecycle(
                    upload_id=upload_id,
                    user_id=str(user.id),
                    redis=fake_redis_setup,
                    db=session,
                    reason="Cancelled by user",
                    delete_object_fn=delete_object,
                    release_reservation_fn=release_reservation,
                )

    with (
        patch(
            "app.workers.process_upload_post_scan.redis_lock",
            new=shared_lock,
        ),
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            new=AsyncMock(return_value=download_result),
        ),
        patch(
            "app.workers.process_upload_post_scan.run_strip_only",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.workers.process_upload_post_scan.UploadCacheRepository.emit_event",
            new=blocked_emit,
        ),
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.get_auth_config",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.maybe_dispatch_webhook",
            new=webhook,
        ),
        patch(
            "app.workers.process_upload_post_scan._trigger_pending_auto_merges",
            new=auto_merge,
        ),
        patch(
            "app.workers.process_upload_post_scan.delete_object",
            new=delete_object,
        ),
    ):
        post_scan_task = asyncio.create_task(
            process_upload_post_scan(
                {
                    "redis": fake_redis_setup,
                    "db_sessionmaker": database.async_session_factory,
                    "job_try": 1,
                },
                upload_id=upload_id,
                user_id=str(user.id),
                quarantine_key=quarantine_key,
                original_filename="document.pdf",
                mime_type="application/pdf",
                original_sha256=original_sha256,
                cas_key="upload:cas:post-scan-serialized",
                cas_s3_key=row.final_key,
                initial_size=128,
            )
        )
        await asyncio.wait_for(emit_entered.wait(), timeout=2)
        cancellation_task = asyncio.create_task(cancel())
        await asyncio.sleep(0)
        assert not cancellation_task.done()
        release_emit.set()
        await asyncio.gather(post_scan_task, cancellation_task)

    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.cas_ref_count == 0
    assert await fake_redis_setup.get(status_key) is None
    assert await fake_redis_setup.get(f"upload:eventlog:{quarantine_key}") is None
    webhook.assert_awaited_once_with(upload_id)
    auto_merge.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "cas_ref_count"),
    [
        ("cancelled", 1),
        ("failed", 1),
        ("malicious", 1),
        ("applied", 1),
        ("clean", 0),
    ],
)
async def test_check_exists_discards_stale_personal_dedup_entries(
    status: str,
    cas_ref_count: int,
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.routers.upload.status import check_file_exists
    from app.schemas.material import CheckExistsRequest

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    sha256 = "5" * 64
    file_key = f"cas/stale-{status}-{cas_ref_count}"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.sha256 = sha256
    row.content_sha256 = "6" * 64
    row.final_key = file_key
    row.cas_ref_count = cas_ref_count
    row.status = status
    await db_session.commit()

    cache_key = f"upload:sha256:{user.id}:{sha256}"
    await fake_redis_setup.set(cache_key, file_key)

    with patch(
        "app.core.storage.facade.object_exists",
        new=AsyncMock(return_value=True),
    ) as object_exists:
        response = await check_file_exists(
            CheckExistsRequest(sha256=sha256, size=128),
            user,
            fake_redis_setup,
            db_session,
        )

    assert response.exists is False
    assert response.file_key is None
    assert await fake_redis_setup.get(cache_key) is None
    object_exists.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_status_does_not_resurrect_cached_clean_after_cancellation(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.routers.upload.status import batch_upload_status
    from app.schemas.material import BatchStatusRequest

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.final_key = "cas/cancelled-status"
    row.cas_ref_count = 0
    row.status = "cancelled"
    row.error_detail = "Cancelled by user"
    await db_session.commit()

    status_key = f"upload:status:{quarantine_key}"
    await fake_redis_setup.set(
        status_key,
        json.dumps(
            {
                "file_key": quarantine_key,
                "status": "clean",
                "result": {"file_key": row.final_key, "size": 128},
            }
        ),
    )

    response = await batch_upload_status(
        BatchStatusRequest(file_keys=[quarantine_key]),
        user,
        fake_redis_setup,
    )

    assert response["statuses"][quarantine_key]["status"] == "failed"
    assert response["statuses"][quarantine_key]["detail"] == "Cancelled by user"
    assert await fake_redis_setup.get(status_key) is None


@pytest.mark.asyncio
async def test_single_part_presigned_completion_rejects_database_cancelled_intent(
    fake_redis_setup: Any,
) -> None:
    from types import SimpleNamespace

    from app.core.common.exceptions import ConflictError
    from app.routers.upload.presigned import complete_upload
    from app.schemas.material import UploadCompleteRequest

    upload_id = str(uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    await fake_redis_setup.set(
        f"upload:intent:{upload_id}",
        json.dumps(
            {
                "user_id": str(user.id),
                "upload_id": upload_id,
                "quarantine_key": quarantine_key,
                "filename": "document.pdf",
                "mime_type": "application/pdf",
                "size": 128,
            }
        ),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value="cancelled")
    _locks, shared_lock = _serialized_lock()

    with (
        patch("app.routers.upload.presigned.redis_lock", new=shared_lock),
        patch(
            "app.routers.upload.presigned.get_object_info",
            new_callable=AsyncMock,
        ) as get_object_info,
    ):
        with pytest.raises(ConflictError):
            await complete_upload(
                UploadCompleteRequest(
                    upload_id=upload_id,
                    quarantine_key=quarantine_key,
                ),
                user,
                fake_redis_setup,
                db,
            )

    get_object_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_cas_hint_does_not_create_personal_dedup_ownership(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.core.security.cas import hmac_cas_key
    from app.routers.upload.status import check_file_exists
    from app.schemas.material import CheckExistsRequest

    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        display_name="Global CAS hint tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db_session.add(user)
    await db_session.commit()

    sha256 = "7" * 64
    file_key = "cas/global-hint"
    await fake_redis_setup.set(
        hmac_cas_key(sha256),
        json.dumps({"ref_count": 1, "final_key": file_key, "size": 128}),
    )

    with patch(
        "app.core.storage.facade.object_exists",
        new=AsyncMock(return_value=True),
    ):
        response = await check_file_exists(
            CheckExistsRequest(sha256=sha256, size=128),
            user,
            fake_redis_setup,
            db_session,
        )

    assert response.exists is True
    assert response.file_key is None
    assert await fake_redis_setup.get(f"upload:sha256:{user.id}:{sha256}") is None


@pytest.mark.asyncio
async def test_cancellation_rejects_upload_claimed_by_open_pr(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.core.common.exceptions import ConflictError
    from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
    from app.models.security import VirusScanResult
    from app.routers.upload.cancellation import cancel_upload_lifecycle

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.status = "clean"
    row.final_key = "cas/open-pr-claim"
    row.content_sha256 = "9" * 64
    row.cas_ref_count = 1
    pr = PullRequest(
        type="batch",
        status=PRStatus.OPEN,
        title="Claimed upload",
        description="",
        payload=[
            {
                "op": "create_material",
                "file_key": row.final_key,
                "content_sha256": row.content_sha256,
            }
        ],
        summary_types=["create_material"],
        author_id=user.id,
        virus_scan_result=VirusScanResult.CLEAN,
    )
    db_session.add(pr)
    await db_session.flush()
    db_session.add(PRFileClaim(file_key=row.final_key, pr_id=pr.id))
    await db_session.commit()

    delete_object = AsyncMock()
    release_reservation = AsyncMock()
    with pytest.raises(ConflictError, match="open contribution"):
        await cancel_upload_lifecycle(
            upload_id=upload_id,
            user_id=str(user.id),
            redis=fake_redis_setup,
            db=db_session,
            reason="Cancelled by user",
            delete_object_fn=delete_object,
            release_reservation_fn=release_reservation,
        )

    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "clean"
    assert row.cas_ref_count == 1
    assert await fake_redis_setup.get(f"upload:cancel:{upload_id}") is None
    delete_object.assert_not_awaited()
    release_reservation.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_upload_rows_cannot_satisfy_missing_pr_key(
    db_session: AsyncSession,
) -> None:
    from app.core.common.exceptions import ConflictError
    from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
    from app.models.security import VirusScanResult
    from app.services.pr import _lock_and_validate_pr_cas_files

    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        display_name="Exact key-set tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db_session.add(user)
    await db_session.flush()

    key_a = "cas/key-a"
    key_b = "cas/key-b"
    for suffix in ("a1", "a2"):
        db_session.add(
            Upload(
                upload_id=f"upload-{suffix}-{uuid.uuid4()}",
                user_id=user.id,
                quarantine_key=f"quarantine/{user.id}/{suffix}/file.pdf",
                final_key=key_a,
                filename="a.pdf",
                status="clean",
                processing_status="complete",
                content_sha256="a" * 64,
                cas_ref_count=1,
            )
        )
    db_session.add(
        Upload(
            upload_id=f"upload-b-{uuid.uuid4()}",
            user_id=user.id,
            quarantine_key=f"quarantine/{user.id}/b/file.pdf",
            final_key=key_b,
            filename="b.pdf",
            status="clean",
            processing_status="pending",
            content_sha256="b" * 64,
            cas_ref_count=1,
        )
    )
    pr = PullRequest(
        type="batch",
        status=PRStatus.OPEN,
        title="Two-key PR",
        description="",
        payload=[
            {"op": "create_material", "file_key": key_a},
            {"op": "create_material", "file_key": key_b},
        ],
        summary_types=["create_material"],
        author_id=user.id,
        virus_scan_result=VirusScanResult.CLEAN,
        auto_merge_pending=True,
    )
    db_session.add(pr)
    await db_session.flush()
    db_session.add_all(
        [
            PRFileClaim(file_key=key_a, pr_id=pr.id),
            PRFileClaim(file_key=key_b, pr_id=pr.id),
        ]
    )
    await db_session.commit()

    with pytest.raises(ConflictError, match="no longer clean"):
        await _lock_and_validate_pr_cas_files(
            db_session,
            pr,
            settled_statuses=frozenset({"complete", "degraded"}),
        )


@pytest.mark.asyncio
async def test_processing_status_database_failure_is_retryable() -> None:
    repo = UploadWorkerRepository(WorkerContext(redis=AsyncMock(), db_sessionmaker=MagicMock()))
    with patch(
        "app.workers.upload.repository._retry_db",
        new=AsyncMock(side_effect=OSError("database unavailable")),
    ):
        with pytest.raises(OSError, match="database unavailable"):
            await repo.update_processing_status("upload-db-outage", "running")


@pytest.mark.asyncio
async def test_status_and_sse_override_stale_clean_after_cancellation(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from fastapi import Request

    from app.routers.upload.sse import upload_events, upload_status
    from app.schemas.material import UploadStatus

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.final_key = "cas/cancelled-status"
    row.status = "cancelled"
    row.error_detail = "Cancelled by user"
    row.cas_ref_count = 0
    await db_session.commit()

    stale_clean = json.dumps(
        {
            "upload_id": upload_id,
            "file_key": quarantine_key,
            "status": "clean",
            "detail": "Success",
            "result": {"file_key": row.final_key, "size": 128, "original_size": 128},
        }
    )
    await fake_redis_setup.set(f"upload:status:{quarantine_key}", stale_clean)
    await fake_redis_setup.rpush(f"upload:eventlog:{quarantine_key}", stale_clean)

    status = await upload_status(
        quarantine_key,
        user,
        fake_redis_setup,
        db_session,
    )
    assert status.status == UploadStatus.FAILED
    assert status.result is None

    request = MagicMock(spec=Request)
    request.headers = {}
    response = await upload_events(
        quarantine_key,
        request,
        user,
        fake_redis_setup,
        db_session,
    )
    event = await anext(aiter(response.body_iterator))
    event_payload = json.loads(event["data"])
    assert event_payload["status"] == "failed"
    assert event_payload["result"] is None


def test_thumbnail_keys_are_upload_specific() -> None:
    import inspect

    from app.workers import process_upload_post_scan, retroactive_quarantine
    from app.workers.upload.stages import finalize

    post_scan_source = inspect.getsource(process_upload_post_scan.process_upload_post_scan)
    finalize_source = inspect.getsource(finalize.run_finalize_storage)
    assert 'f"thumbnails/{cas_id}/{upload_id}.webp"' in post_scan_source
    assert 'f"thumbnails/{cas_id}/{input_data.upload_id}.webp"' in finalize_source
    assert retroactive_quarantine._thumbnail_owned_by_upload(
        "thumbnails/cas-id/upload-123.webp", "upload-123"
    )
    assert not retroactive_quarantine._thumbnail_owned_by_upload(
        "thumbnails/cas-id.webp", "upload-123"
    )


@pytest.mark.asyncio
async def test_retroactive_quarantine_serializes_after_post_scan_clean_event(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from types import SimpleNamespace

    import app.core.database.database as database
    from app.workers.process_upload_post_scan import process_upload_post_scan
    from app.workers.retroactive_quarantine import retroactive_quarantine
    from app.workers.upload.cache_repo import UploadCacheRepository

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.final_key = "cas/retroactive-race"
    row.sha256 = "3" * 64
    row.content_sha256 = "4" * 64
    row.cas_ref_count = 1
    row.status = "clean"
    row.processing_status = "pending"
    await db_session.commit()

    initial_clean = json.dumps(
        {
            "upload_id": upload_id,
            "file_key": quarantine_key,
            "status": "clean",
            "detail": "File ready",
            "result": {
                "file_key": row.final_key,
                "size": 128,
                "original_size": 128,
                "mime_type": "application/pdf",
                "file_name": "document.pdf",
            },
        }
    )
    await fake_redis_setup.set(f"upload:status:{quarantine_key}", initial_clean)
    await fake_redis_setup.rpush(f"upload:eventlog:{quarantine_key}", initial_clean)

    pf = MagicMock()
    pf.path = Path("/tmp/retroactive-race.pdf")
    pf.cleanup = MagicMock()
    download_result = SimpleNamespace(pf=pf, actual_mime="application/pdf")

    _locks, shared_lock = _serialized_lock()
    clean_emit_entered = asyncio.Event()
    release_clean_emit = asyncio.Event()
    original_emit = UploadCacheRepository.emit_event

    async def blocked_clean_emit(
        cache: UploadCacheRepository,
        status_key: str,
        event_channel: str,
        event_log_key: str,
        payload_json: str,
    ) -> None:
        if json.loads(payload_json).get("status") == "clean":
            clean_emit_entered.set()
            await release_clean_emit.wait()
        await original_emit(cache, status_key, event_channel, event_log_key, payload_json)

    with (
        patch("app.workers.process_upload_post_scan.redis_lock", new=shared_lock),
        patch("app.workers.retroactive_quarantine.redis_lock", new=shared_lock),
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            new=AsyncMock(return_value=download_result),
        ),
        patch(
            "app.workers.process_upload_post_scan.run_strip_only",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan.run_thumbnail_stage",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.workers.process_upload_post_scan.UploadCacheRepository.emit_event",
            new=blocked_clean_emit,
        ),
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.get_auth_config",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.maybe_dispatch_webhook",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan._trigger_pending_auto_merges",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan.delete_object",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.retroactive_quarantine.dispatch_post_commit_actions",
            new_callable=AsyncMock,
        ),
        patch("app.workers.retroactive_quarantine.settings") as quarantine_settings,
    ):
        quarantine_settings.bazaar_retroactive_check_materials = False
        post_scan_task = asyncio.create_task(
            process_upload_post_scan(
                {
                    "redis": fake_redis_setup,
                    "db_sessionmaker": database.async_session_factory,
                    "job_try": 1,
                },
                upload_id=upload_id,
                user_id=str(user.id),
                quarantine_key=quarantine_key,
                original_filename="document.pdf",
                mime_type="application/pdf",
                original_sha256="3" * 64,
                cas_key="upload:cas:retroactive-race",
                cas_s3_key=row.final_key,
                initial_size=128,
            )
        )
        await asyncio.wait_for(clean_emit_entered.wait(), timeout=2)

        quarantine_task = asyncio.create_task(
            retroactive_quarantine(
                WorkerContext(
                    redis=fake_redis_setup,
                    db_sessionmaker=database.async_session_factory,
                ),
                upload_id=upload_id,
                sha256="3" * 64,
                cas_s3_key=row.final_key,
                user_id=str(user.id),
                threat="Race.Test.Malware",
            )
        )
        await asyncio.sleep(0)
        assert not quarantine_task.done()

        release_clean_emit.set()
        await asyncio.gather(post_scan_task, quarantine_task)

    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "malicious"
    assert row.cas_ref_count == 0

    cached = await fake_redis_setup.get(f"upload:status:{quarantine_key}")
    assert cached is not None
    cached_payload = json.loads(cached)
    assert cached_payload["status"] == "malicious"

    log_entries = await fake_redis_setup.lrange(f"upload:eventlog:{quarantine_key}", 0, -1)
    assert log_entries
    assert json.loads(log_entries[-1])["status"] == "malicious"


@pytest.mark.asyncio
async def test_auto_merge_revalidates_after_malware_transition(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    import app.core.database.database as database
    from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
    from app.models.security import VirusScanResult
    from app.workers.process_upload_post_scan import _trigger_pending_auto_merges

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    key = "cas/auto-merge-malware-race"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.final_key = key
    row.status = "clean"
    row.processing_status = "complete"
    row.content_sha256 = "5" * 64
    row.cas_ref_count = 1
    pr = PullRequest(
        type="batch",
        status=PRStatus.OPEN,
        title="Malware race PR",
        description="",
        payload=[
            {
                "op": "create_material",
                "file_key": key,
                "content_sha256": row.content_sha256,
            }
        ],
        summary_types=["create_material"],
        author_id=user.id,
        virus_scan_result=VirusScanResult.CLEAN,
        auto_merge_pending=True,
    )
    db_session.add(pr)
    await db_session.flush()
    db_session.add(PRFileClaim(file_key=key, pr_id=pr.id))
    await db_session.commit()

    transition_count = 0

    @asynccontextmanager
    async def malware_wins_before_validation(
        _ctx: WorkerContext, locked_upload_id: str
    ) -> AsyncIterator[None]:
        nonlocal transition_count
        assert locked_upload_id == upload_id
        transition_count += 1
        async with database.async_session_factory() as transition_db:
            transitioning = await transition_db.scalar(
                select(Upload).where(Upload.upload_id == upload_id).with_for_update()
            )
            assert transitioning is not None
            transitioning.status = "malicious"
            transitioning.cas_ref_count = 0
            await transition_db.commit()
        yield

    apply_pr = AsyncMock()
    cleanup = AsyncMock()
    with (
        patch(
            "app.workers.process_upload_post_scan._post_scan_lifecycle_guard",
            new=malware_wins_before_validation,
        ),
        patch("app.workers.process_upload_post_scan.apply_pr", new=apply_pr),
        patch(
            "app.workers.process_upload_post_scan._cleanup_pr_resources",
            new=cleanup,
        ),
    ):
        await _trigger_pending_auto_merges(
            WorkerContext(
                redis=fake_redis_setup,
                db_sessionmaker=database.async_session_factory,
            ),
            key,
        )

    assert transition_count == 1
    apply_pr.assert_not_awaited()
    cleanup.assert_not_awaited()
    db_session.expire_all()
    await db_session.refresh(row)
    await db_session.refresh(pr)
    assert row.status == "malicious"
    assert pr.status == PRStatus.OPEN
    assert pr.auto_merge_pending is True


@pytest.mark.asyncio
async def test_auto_merge_holds_lifecycle_lock_through_apply_and_claim_transfer(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    import app.core.database.database as database
    from app.core.common.exceptions import ConflictError
    from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
    from app.models.security import VirusScanResult
    from app.routers.upload.cancellation import (
        cancel_upload_lifecycle,
        upload_lifecycle_lock_name,
    )
    from app.workers.process_upload_post_scan import _trigger_pending_auto_merges

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    key = "cas/auto-merge-apply-lock"
    content_sha256 = "6" * 64
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.final_key = key
    row.status = "clean"
    row.processing_status = "complete"
    row.content_sha256 = content_sha256
    row.cas_ref_count = 1
    pr = PullRequest(
        type="batch",
        status=PRStatus.OPEN,
        title="Apply lock PR",
        description="",
        payload=[
            {
                "op": "create_material",
                "file_key": key,
                "content_sha256": content_sha256,
            }
        ],
        summary_types=["create_material"],
        author_id=user.id,
        virus_scan_result=VirusScanResult.CLEAN,
        auto_merge_pending=True,
    )
    db_session.add(pr)
    await db_session.flush()
    db_session.add(PRFileClaim(file_key=key, pr_id=pr.id))
    await db_session.commit()

    _locks, shared_lock = _serialized_lock()
    apply_entered = asyncio.Event()
    release_apply = asyncio.Event()

    async def blocked_apply(*_args: Any, **_kwargs: Any) -> None:
        apply_entered.set()
        await release_apply.wait()

    delete_object = AsyncMock()
    release_reservation = AsyncMock()

    async def cancel_after_validation() -> None:
        async with shared_lock(
            fake_redis_setup,
            upload_lifecycle_lock_name(upload_id),
        ):
            async with database.async_session_factory() as cancel_db:
                await cancel_upload_lifecycle(
                    upload_id=upload_id,
                    user_id=str(user.id),
                    redis=fake_redis_setup,
                    db=cancel_db,
                    reason="Cancelled by user",
                    delete_object_fn=delete_object,
                    release_reservation_fn=release_reservation,
                )

    with (
        patch("app.workers.process_upload_post_scan.redis_lock", new=shared_lock),
        patch("app.workers.process_upload_post_scan.apply_pr", new=blocked_apply),
        patch(
            "app.workers.process_upload_post_scan.persist_post_commit_jobs",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan.dispatch_post_commit_actions",
            new_callable=AsyncMock,
        ),
        patch(
            "app.workers.process_upload_post_scan.notify_user",
            new_callable=AsyncMock,
        ),
    ):
        auto_merge_task = asyncio.create_task(
            _trigger_pending_auto_merges(
                WorkerContext(
                    redis=fake_redis_setup,
                    db_sessionmaker=database.async_session_factory,
                ),
                key,
            )
        )
        await asyncio.wait_for(apply_entered.wait(), timeout=2)
        cancel_task = asyncio.create_task(cancel_after_validation())
        await asyncio.sleep(0)
        assert not cancel_task.done()

        release_apply.set()
        await auto_merge_task
        with pytest.raises(ConflictError, match="already been applied"):
            await cancel_task

    db_session.expire_all()
    await db_session.refresh(row)
    await db_session.refresh(pr)
    assert pr.status == PRStatus.APPROVED
    assert row.status == "applied"
    assert row.cas_ref_count == 0
    delete_object.assert_not_awaited()
    release_reservation.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_quota_cleanup_never_revives_malicious_upload(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.models.pull_request import PRStatus, PullRequest
    from app.models.security import VirusScanResult
    from app.services.pr import _release_pr_upload_quota

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    key = "cas/malicious-pr-cleanup"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.final_key = key
    row.status = "malicious"
    row.cas_ref_count = 0
    pr = PullRequest(
        type="batch",
        status=PRStatus.APPROVED,
        title="Malicious cleanup PR",
        description="",
        payload=[{"op": "create_material", "file_key": key}],
        summary_types=["create_material"],
        author_id=user.id,
        virus_scan_result=VirusScanResult.INFECTED,
    )
    db_session.add(pr)
    await db_session.commit()

    await _release_pr_upload_quota(
        db_session,
        pr,
        fake_redis_setup,
        approved=True,
    )
    await db_session.commit()

    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "malicious"


@pytest.mark.asyncio
async def test_cancellation_removes_upload_specific_thumbnail(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    from app.routers.upload.cancellation import cancel_upload_lifecycle

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    thumbnail_key = f"thumbnails/cas-id/{upload_id}.webp"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.status = "clean"
    row.thumbnail_key = thumbnail_key
    row.thumbnail_status = "ok"
    row.cas_ref_count = 0
    await db_session.commit()

    delete_object = AsyncMock()
    await cancel_upload_lifecycle(
        upload_id=upload_id,
        user_id=str(user.id),
        redis=fake_redis_setup,
        db=db_session,
        reason="Cancelled by user",
        delete_object_fn=delete_object,
        release_reservation_fn=AsyncMock(),
    )

    deleted_keys = {call.args[0] for call in delete_object.await_args_list}
    assert deleted_keys == {quarantine_key, thumbnail_key}
    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.thumbnail_key is None
    assert row.thumbnail_status == "failed"


@pytest.mark.asyncio
async def test_retroactive_quarantine_retry_repairs_malicious_cache(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    """A retry after the DB commit must still replace stale CLEAN Redis state."""
    import app.core.database.database as database
    from app.workers.retroactive_quarantine import retroactive_quarantine

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.final_key = "cas/already-malicious"
    row.status = "malicious"
    row.error_detail = "MalwareBazaar retroactive hit: Retry.Test"
    row.cas_ref_count = 0
    await db_session.commit()

    stale_clean = json.dumps(
        {
            "upload_id": upload_id,
            "file_key": quarantine_key,
            "status": "clean",
            "detail": "File ready",
            "result": {"file_key": row.final_key},
        }
    )
    await fake_redis_setup.set(f"upload:status:{quarantine_key}", stale_clean)
    await fake_redis_setup.rpush(f"upload:eventlog:{quarantine_key}", stale_clean)

    @asynccontextmanager
    async def no_op_lock(*_args: Any, **_kwargs: Any) -> AsyncIterator[None]:
        yield

    with (
        patch("app.workers.retroactive_quarantine.redis_lock", new=no_op_lock),
        patch("app.workers.retroactive_quarantine.settings") as quarantine_settings,
    ):
        quarantine_settings.bazaar_retroactive_check_materials = False
        await retroactive_quarantine(
            WorkerContext(
                redis=fake_redis_setup,
                db_sessionmaker=database.async_session_factory,
            ),
            upload_id=upload_id,
            sha256="7" * 64,
            cas_s3_key=row.final_key,
            user_id=str(user.id),
            threat="Retry.Test",
        )

    cached = await fake_redis_setup.get(f"upload:status:{quarantine_key}")
    assert cached is not None
    assert json.loads(cached)["status"] == "malicious"
    log_entries = await fake_redis_setup.lrange(f"upload:eventlog:{quarantine_key}", 0, -1)
    assert json.loads(log_entries[-1])["status"] == "malicious"


@pytest.mark.asyncio
async def test_cancellation_winning_lifecycle_lock_suppresses_deferred_webhook(
    db_session: AsyncSession,
    fake_redis_setup: Any,
) -> None:
    """A queued webhook must revalidate only after cancellation releases the lock."""
    import app.core.database.database as database
    from app.core.security.cas import hmac_cas_key
    from app.core.security.url_validation import ResolvedHttpsUrl
    from app.routers.upload.cancellation import (
        cancel_upload_lifecycle,
        upload_lifecycle_lock_name,
    )
    from app.workers.webhook_dispatch import dispatch_webhook

    upload_id = str(uuid.uuid4())
    user = await _create_user_and_upload(
        db_session,
        upload_id=upload_id,
        quarantine_key=f"quarantine/pending/{upload_id}/document.pdf",
        size=128,
    )
    quarantine_key = f"quarantine/{user.id}/{upload_id}/document.pdf"
    content_sha256 = "8" * 64
    row = await db_session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    assert row is not None
    row.quarantine_key = quarantine_key
    row.final_key = "cas/webhook-cancel-race"
    row.sha256 = "9" * 64
    row.content_sha256 = content_sha256
    row.cas_ref_count = 1
    row.status = "clean"
    row.webhook_url = "https://example.com/hook"
    await db_session.commit()
    await fake_redis_setup.set(
        hmac_cas_key(content_sha256),
        json.dumps({"ref_count": 1, "final_key": row.final_key, "size": 128}),
    )

    _locks, shared_lock = _serialized_lock()
    delete_entered = asyncio.Event()
    release_delete = asyncio.Event()

    async def blocked_delete(_key: str) -> None:
        delete_entered.set()
        await release_delete.wait()

    async def cancellation() -> None:
        async with shared_lock(
            fake_redis_setup,
            upload_lifecycle_lock_name(upload_id),
        ):
            async with database.async_session_factory() as cancel_db:
                await cancel_upload_lifecycle(
                    upload_id=upload_id,
                    user_id=str(user.id),
                    redis=fake_redis_setup,
                    db=cancel_db,
                    reason="Cancelled by user",
                    delete_object_fn=blocked_delete,
                    release_reservation_fn=AsyncMock(),
                )

    post = AsyncMock()
    target = ResolvedHttpsUrl(
        "https://example.com/hook",
        "example.com",
        ("93.184.216.34",),
    )
    with (
        patch("app.workers.webhook_dispatch.redis_lock", new=shared_lock),
        patch(
            "app.workers.webhook_dispatch.resolve_safe_url_async",
            new=AsyncMock(return_value=target),
        ),
        patch("app.workers.webhook_dispatch.post_pinned_https", new=post),
    ):
        cancel_task = asyncio.create_task(cancellation())
        await asyncio.wait_for(delete_entered.wait(), timeout=2)
        webhook_task = asyncio.create_task(
            dispatch_webhook(
                {
                    "redis": fake_redis_setup,
                    "db_sessionmaker": database.async_session_factory,
                },
                upload_id=upload_id,
            )
        )
        await asyncio.sleep(0)
        assert not webhook_task.done()
        post.assert_not_awaited()

        release_delete.set()
        await asyncio.gather(cancel_task, webhook_task)

    post.assert_not_awaited()
    db_session.expire_all()
    await db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.cas_ref_count == 0


@pytest.mark.asyncio
async def test_thumbnail_resolution_uses_contribution_owner_upload(
    db_session: AsyncSession,
) -> None:
    from app.services.pr import _resolve_thumbnail_info

    key = "cas/shared-thumbnail-content"
    owner_upload_id = str(uuid.uuid4())
    owner = await _create_user_and_upload(
        db_session,
        upload_id=owner_upload_id,
        quarantine_key=f"quarantine/pending/{owner_upload_id}/document.pdf",
        size=128,
    )
    owner_row = await db_session.scalar(select(Upload).where(Upload.upload_id == owner_upload_id))
    assert owner_row is not None
    owner_row.final_key = key
    owner_row.status = "clean"
    owner_row.cas_ref_count = 1
    owner_row.thumbnail_key = f"thumbnails/shared/{owner_upload_id}.webp"
    owner_row.thumbnail_status = "ok"

    other_upload_id = str(uuid.uuid4())
    other = await _create_user_and_upload(
        db_session,
        upload_id=other_upload_id,
        quarantine_key=f"quarantine/pending/{other_upload_id}/document.pdf",
        size=128,
    )
    other_row = await db_session.scalar(select(Upload).where(Upload.upload_id == other_upload_id))
    assert other_row is not None
    other_row.final_key = key
    other_row.status = "clean"
    other_row.cas_ref_count = 1
    other_row.thumbnail_key = f"thumbnails/shared/{other_upload_id}.webp"
    other_row.thumbnail_status = "ok"
    await db_session.commit()

    thumbnail_key, thumbnail_status = await _resolve_thumbnail_info(
        db_session,
        key,
        owner.id,
    )
    assert thumbnail_key == owner_row.thumbnail_key
    assert thumbnail_status == "ok"


@pytest.mark.asyncio
async def test_auto_merge_infrastructure_failure_is_retryable() -> None:
    from app.workers.process_upload_post_scan import _trigger_pending_auto_merges

    def unavailable_session_factory() -> Any:
        raise OSError("database unavailable")

    with pytest.raises(OSError, match="database unavailable"):
        await _trigger_pending_auto_merges(
            WorkerContext(
                redis=AsyncMock(),
                db_sessionmaker=unavailable_session_factory,
            ),
            "cas/retry-auto-merge",
        )


def test_deferred_webhook_holds_row_lock_through_network_delivery() -> None:
    import inspect

    from app.workers.webhook_dispatch import _deliver_webhook_once

    source = inspect.getsource(_deliver_webhook_once)
    assert source.index("with_for_update()") < source.index("post_pinned_https(")


@pytest.mark.asyncio
async def test_post_scan_start_database_failure_raises_for_arq_retry() -> None:
    from app.workers.process_upload_post_scan import process_upload_post_scan

    download = AsyncMock()
    with (
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.update_processing_status",
            new=AsyncMock(side_effect=OSError("database unavailable")),
        ),
        patch(
            "app.workers.process_upload_post_scan.run_download_and_validate",
            new=download,
        ),
    ):
        with pytest.raises(OSError, match="database unavailable"):
            await process_upload_post_scan(
                {
                    "redis": AsyncMock(),
                    "db_sessionmaker": MagicMock(),
                    "job_try": 1,
                },
                upload_id="post-scan-db-outage",
                user_id=str(uuid.uuid4()),
                quarantine_key="quarantine/user/post-scan-db-outage/file.pdf",
                original_filename="file.pdf",
                mime_type="application/pdf",
                original_sha256="a" * 64,
                cas_key="upload:cas:post-scan-db-outage",
                cas_s3_key="cas/post-scan-db-outage",
                initial_size=128,
            )

    download.assert_not_awaited()
