"""Regression tests for renewable admission and cancellation/finalization races."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
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
    assert 'f"upload-complete:{data.upload_id}"' in complete_source
    assert 'f"upload-complete:{upload_id}"' in abort_source


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

    assert updated is False
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
        head_task = asyncio.create_task(
            tus_head(uuid.UUID(tus_id), user, fake_redis_setup, db)
        )
        await asyncio.wait_for(enqueue_entered.wait(), timeout=2)
        delete_task = asyncio.create_task(
            tus_delete(uuid.UUID(tus_id), user, fake_redis_setup, db)
        )
        await asyncio.sleep(0)
        assert not delete_task.done()
        release_enqueue.set()
        head_response, delete_response = await asyncio.gather(head_task, delete_task)

    assert head_response.status_code == 200
    assert delete_response.status_code == 204
    delete_object.assert_awaited_once_with(quarantine_key)
    assert await fake_redis_setup.hgetall(state_key) == {}
    assert await fake_redis_setup.get(f"upload:cancel:{upload_id}") is not None
    db.execute.assert_awaited()
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
        complete_response, abort_response = await asyncio.gather(
            complete_task, abort_task
        )

    assert complete_response.status.value == "processing"
    assert abort_response is None
    delete_object.assert_awaited_once_with(quarantine_key)
    assert await fake_redis_setup.get(intent_key) is None
    assert await fake_redis_setup.get(f"upload:cancel:{upload_id}") is not None
    db.execute.assert_awaited()
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
        response = await client.delete(
            f"/api/upload/tus/{tus_id}", headers=_auth_headers(user)
        )

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
