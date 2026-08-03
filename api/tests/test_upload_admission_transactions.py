"""Regression tests for upload admission and transaction boundaries."""

import asyncio
import base64
import io
import uuid
import zipfile
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.config import settings
from app.core.common.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.core.database.post_commit import dispatch_pending_outbox, persist_post_commit_jobs
from app.models.user import User, UserRole
from app.routers.upload.helpers import _queue_processing_after_commit, _reserve_storage_limit
from app.schemas.material import UploadInitRequest


async def _create_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        display_name="Upload admission tester",
        role=UserRole.STUDENT,
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
async def test_capacity_reservation_reads_usage_atomically_after_cache_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CAS finalize racing cache initialization must affect admission."""

    class RacingRedis:
        def __init__(self) -> None:
            self.usage = 0

        async def get(self, key: str) -> int | None:
            return self.usage or None

        async def set(self, key: str, value: int, **kwargs: object) -> bool:
            assert kwargs.get("nx") is True
            # A concurrent CAS finalize wins initialization after the DB read.
            self.usage = 95
            return False

        def register_script(self, _script: str):
            async def reserve(*, keys, args, client):  # type: ignore[no-untyped-def]
                assert client is self
                assert keys[-1] == "storage:total_usage_bytes"
                requested = int(args[1])
                capacity = int(args[4])
                return int(self.usage + requested <= capacity)

            return reserve

    db = AsyncMock()
    db.scalar.return_value = 80
    monkeypatch.setattr(settings, "max_storage_gb", 100 / (1024**3))

    with pytest.raises(BadRequestError):
        await _reserve_storage_limit(10, "upload-1", cast(Any, RacingRedis()), db)


@pytest.mark.asyncio
async def test_processing_job_is_not_dispatched_before_outbox_commit(
    db_session: AsyncSession,
) -> None:
    pool = AsyncMock()
    with patch("app.core.database.redis.arq_pool", pool):
        _queue_processing_after_commit(
            db_session,
            "user-1",
            "upload-1",
            "quarantine/user-1/upload-1/file.pdf",
            "file.pdf",
            "application/pdf",
            trace_context={},
        )
        await persist_post_commit_jobs(db_session)
        pool.enqueue_job.assert_not_awaited()

        await db_session.commit()
        await dispatch_pending_outbox(db_session)

    pool.enqueue_job.assert_awaited_once()
    assert pool.enqueue_job.await_args.args == ("process_upload",)
    assert pool.enqueue_job.await_args.kwargs["user_id"] == "user-1"
    assert pool.enqueue_job.await_args.kwargs["_queue_name"] == "upload-slow"


@pytest.mark.asyncio
async def test_direct_idempotency_key_cannot_cross_users(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    first_user = await _create_user(db_session)
    second_user = await _create_user(db_session)
    external_id = str(uuid.uuid4())
    cache: dict[str, str] = {}

    async def cache_get(key: str) -> str | None:
        return cache.get(key)

    async def cache_set(key: str, value: str, **_kwargs: object) -> None:
        cache[key] = value

    mock_redis.get.side_effect = cache_get
    mock_redis.set.side_effect = cache_set

    with (
        patch("app.routers.upload.direct.get_s3_client") as s3_context,
        patch("app.routers.upload.direct._reserve_storage_limit", new_callable=AsyncMock),
    ):
        s3_context.return_value.__aenter__.return_value = AsyncMock()
        first = await client.post(
            "/api/upload",
            files={"file": ("first.pdf", b"%PDF-1.4\nfirst", "application/pdf")},
            headers={**_auth_headers(first_user), "X-Upload-ID": external_id},
        )
        second = await client.post(
            "/api/upload",
            files={"file": ("second.pdf", b"%PDF-1.4\nsecond", "application/pdf")},
            headers={**_auth_headers(second_user), "X-Upload-ID": external_id},
        )

    assert first.status_code == 202
    assert second.status_code == 403
    assert second.json() != first.json()
    assert f"upload:idem:{first_user.id}:{external_id}" in cache


@pytest.mark.asyncio
async def test_batch_entries_never_share_an_async_session(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("one.pdf", b"%PDF-1.4\none")
        output.writestr("two.pdf", b"%PDF-1.4\ntwo")
    archive.seek(0)

    sessions: set[int] = set()
    both_entered = asyncio.Event()

    async def observe_session(*, db: AsyncSession, **_kwargs: object) -> None:
        sessions.add(id(db))
        if len(sessions) == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=2)

    with (
        patch("app.routers.upload.batch_zip._reserve_storage_limit", new_callable=AsyncMock),
        patch("app.routers.upload.batch_zip._check_pending_cap", new_callable=AsyncMock),
        patch("app.routers.upload.batch_zip._create_upload_row", side_effect=observe_session),
        patch("app.routers.upload.batch_zip._queue_processing_after_commit"),
        patch("app.routers.upload.batch_zip.persist_post_commit_jobs", new_callable=AsyncMock),
        patch("app.routers.upload.batch_zip.dispatch_post_commit_actions", new_callable=AsyncMock),
        patch("app.routers.upload.batch_zip.get_s3_client") as s3_context,
    ):
        s3_context.return_value.__aenter__.return_value = AsyncMock()
        response = await client.post(
            "/api/upload/batch-zip",
            files={"file": ("batch.zip", archive.getvalue(), "application/zip")},
            headers=_auth_headers(user),
        )

    assert response.status_code == 202
    assert len(response.json()["files"]) == 2
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_multipart_init_aborts_s3_upload_when_part_setup_fails(
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    from app.routers.upload.presigned import presigned_multipart_init

    user = await _create_user(db_session)
    data = UploadInitRequest(
        filename="large.pdf",
        size=8 * 1024 * 1024,
        mime_type="application/pdf",
    )

    with (
        patch("app.routers.upload.presigned._check_pending_cap", new_callable=AsyncMock),
        patch("app.routers.upload.presigned._reserve_storage_limit", new_callable=AsyncMock),
        patch("app.routers.upload.presigned._release_storage_reservation", new_callable=AsyncMock),
        patch(
            "app.routers.upload.presigned.create_multipart_upload",
            new_callable=AsyncMock,
            return_value="s3-upload-1",
        ),
        patch(
            "app.routers.upload.presigned.generate_presigned_upload_part",
            new_callable=AsyncMock,
            side_effect=RuntimeError("signing failed"),
        ),
        patch(
            "app.routers.upload.presigned.abort_multipart_upload", new_callable=AsyncMock
        ) as abort,
    ):
        with pytest.raises(RuntimeError, match="signing failed"):
            await presigned_multipart_init(data, user, mock_redis, db_session, None)

    abort.assert_awaited_once()
    assert abort.await_args is not None
    assert abort.await_args.args[1] == "s3-upload-1"


@pytest.mark.asyncio
async def test_tus_create_unwinds_multipart_and_reservations_on_state_failure(
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    from app.routers.tus import tus_create

    user = await _create_user(db_session)
    filename = base64.b64encode(b"file.pdf").decode()
    mime = base64.b64encode(b"application/pdf").decode()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/upload/tus",
            "headers": [
                (b"tus-resumable", b"1.0.0"),
                (b"upload-length", b"1024"),
                (b"upload-metadata", f"filename {filename},filetype {mime}".encode()),
            ],
            "scheme": "http",
            "server": ("test", 80),
        }
    )
    mock_redis.hset.side_effect = RuntimeError("redis state failed")

    with (
        patch("app.routers.tus._reserve_storage_limit", new_callable=AsyncMock),
        patch("app.routers.tus._release_storage_reservation", new_callable=AsyncMock) as release,
        patch("app.routers.tus._check_pending_cap", new_callable=AsyncMock),
        patch("app.routers.tus._create_upload_row", new_callable=AsyncMock),
        patch(
            "app.routers.tus.create_multipart_upload",
            new_callable=AsyncMock,
            return_value="s3-tus-1",
        ),
        patch("app.routers.tus.abort_multipart_upload", new_callable=AsyncMock) as abort,
    ):
        with pytest.raises(RuntimeError, match="redis state failed"):
            await tus_create(request, user, mock_redis, db_session, None)

    abort.assert_awaited_once()
    release.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_error",
    [NotFoundError("Upload not found"), ForbiddenError("Foreign upload")],
)
async def test_tus_invalid_or_foreign_resource_is_rejected_before_body_consumption(
    db_session: AsyncSession,
    mock_redis: AsyncMock,
    state_error: Exception,
) -> None:
    from app.routers.tus import tus_patch

    user = await _create_user(db_session)
    body_reads = 0

    async def receive() -> dict[str, object]:
        nonlocal body_reads
        body_reads += 1
        return {"type": "http.request", "body": b"x", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/",
            "headers": [
                (b"content-type", b"application/offset+octet-stream"),
                (b"upload-offset", b"0"),
                (b"content-length", b"1"),
            ],
        },
        receive,
    )

    with patch(
        "app.routers.tus._load_state",
        new_callable=AsyncMock,
        side_effect=state_error,
    ):
        with pytest.raises(type(state_error)):
            await tus_patch(uuid.uuid4(), request, user, mock_redis, db_session)

    assert body_reads == 0


@pytest.mark.asyncio
async def test_tus_completed_upload_can_retry_enqueue_with_zero_byte_patch(
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    from app.routers.tus import tus_patch

    user = await _create_user(db_session)
    tus_id = uuid.uuid4()
    state = {
        "user_id": str(user.id),
        "upload_id": str(uuid.uuid4()),
        "quarantine_key": f"quarantine/{user.id}/upload/file.pdf",
        "s3_upload_id": "s3-id",
        "filename": "file.pdf",
        "mime_type": "application/pdf",
        "offset": "1024",
        "length": "1024",
        "parts": "[]",
        "multipart_completed": "1",
        "sniffed": "1",
    }
    request = Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/",
            "headers": [
                (b"content-type", b"application/offset+octet-stream"),
                (b"upload-offset", b"1024"),
                (b"content-length", b"0"),
            ],
        }
    )
    mock_redis.zrem.return_value = 1

    @asynccontextmanager
    async def unlocked(*_args: object, **_kwargs: object):
        yield

    with (
        patch("app.routers.tus.redis_lock", unlocked),
        patch("app.routers.tus.redis_semaphore", unlocked),
        patch("app.routers.tus._load_state", new_callable=AsyncMock, return_value=state),
        patch("app.routers.tus._enqueue_processing", new_callable=AsyncMock) as enqueue,
    ):
        response = await tus_patch(tus_id, request, user, mock_redis, db_session)

    assert response.status_code == 204
    enqueue.assert_awaited_once()
    assert enqueue.await_args is not None
    assert enqueue.await_args.kwargs["job_id"] == f"tus-process:{state['upload_id']}"
    mock_redis.hset.assert_awaited()
    assert mock_redis.hset.await_args is not None
    assert mock_redis.hset.await_args.kwargs["mapping"]["enqueued"] == "1"


@pytest.mark.asyncio
async def test_tus_delete_releases_all_lifecycle_ownership(
    db_session: AsyncSession,
    mock_redis: AsyncMock,
) -> None:
    from app.routers.tus import tus_delete

    user = await _create_user(db_session)
    upload_id = str(uuid.uuid4())
    state = {
        "user_id": str(user.id),
        "upload_id": upload_id,
        "quarantine_key": f"quarantine/{user.id}/{upload_id}/file.pdf",
        "s3_upload_id": "s3-id",
    }

    with (
        patch("app.routers.tus._load_state", new_callable=AsyncMock, return_value=state),
        patch("app.routers.tus.abort_multipart_upload", new_callable=AsyncMock) as abort,
        patch("app.routers.tus.delete_object", new_callable=AsyncMock) as delete_object,
        patch("app.routers.tus._release_storage_reservation", new_callable=AsyncMock) as release,
    ):
        response = await tus_delete(uuid.uuid4(), user, mock_redis, db_session)

    assert response.status_code == 204
    abort.assert_awaited_once_with(state["quarantine_key"], "s3-id")
    delete_object.assert_awaited_once_with(state["quarantine_key"])
    release.assert_awaited_once_with(upload_id, mock_redis)
    mock_redis.set.assert_awaited_with(f"upload:cancel:{upload_id}", "1", ex=24 * 3600)
    assert mock_redis.zrem.await_count == 2
