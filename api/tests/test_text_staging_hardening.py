from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.upload_errors import UploadErrorCode
from app.core.database.post_commit import PostCommitKey, rollback_transaction_callbacks
from app.models.material import Material, MaterialVersion
from app.routers.materials import (
    _TEXT_EDIT_MAX_BYTES,
    save_material_text_content,
)
from tests.test_materials import _auth_headers, _create_directory, _create_user


@pytest.mark.asyncio
async def test_text_edit_unicode_under_character_limit_but_over_byte_limit_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Emoji body: ~3M chars but ~12 MiB UTF-8. Must be rejected by transport cap."""
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Unicode Test",
        slug="unicode-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/u.md",
        file_name="u.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()

    # ~3 million chars, ~12 MiB UTF-8.
    body = "😀" * (3 * 1024 * 1024)

    response = await client.post(
        f"/api/materials/{material.id}/text-content",
        content=body.encode("utf-8"),
        headers={**_auth_headers(user), "content-type": "text/plain"},
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_text_edit_transport_limit_plus_one_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="ASCII Test",
        slug="ascii-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/a.md",
        file_name="a.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()

    body = b"a" * (_TEXT_EDIT_MAX_BYTES + 1)

    response = await client.post(
        f"/api/materials/{material.id}/text-content",
        content=body,
        headers={**_auth_headers(user), "content-type": "text/plain"},
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_text_edit_respects_configured_text_policy(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    """Even below the 10 MiB editor ceiling, configured max_text_size_mb is enforced."""
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Policy Test",
        slug="policy-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/p.md",
        file_name="p.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()

    from app.config import settings

    monkeypatch.setattr(settings, "max_text_size_mb", 1)

    body = b"a" * (2 * 1024 * 1024)

    with (
        patch("app.routers.materials.read_full_object", new=AsyncMock(return_value=b"old")),
        patch("app.routers.materials.storage_upload_file", new_callable=AsyncMock),
        patch("app.routers.materials._check_pending_cap", new_callable=AsyncMock),
        patch("app.routers.materials._reserve_storage_limit", new_callable=AsyncMock),
    ):
        response = await client.post(
            f"/api/materials/{material.id}/text-content",
            content=body,
            headers={**_auth_headers(user), "content-type": "text/plain"},
        )

    assert response.status_code == 400
    data = response.json()
    assert data.get("error_code") == UploadErrorCode.FILE_TOO_LARGE


@pytest.mark.asyncio
async def test_text_staging_reserves_pending_quota(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Quota Test",
        slug="quota-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/q.md",
        file_name="q.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()
    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    check_pending = AsyncMock()
    reserve_storage = AsyncMock()
    mock_redis = MagicMock()

    with (
        patch("app.routers.materials.read_full_object", new=AsyncMock(return_value=b"old")),
        patch("app.routers.materials.storage_upload_file", new_callable=AsyncMock),
        patch("app.routers.materials._check_pending_cap", check_pending),
        patch("app.routers.materials._reserve_storage_limit", reserve_storage),
    ):
        await save_material_text_content(str(material.id), user, db_session, "new text", mock_redis)

    check_pending.assert_awaited_once()
    call_kwargs = check_pending.call_args
    # The reserve_key should start with "staging:"
    assert call_kwargs.kwargs["reserve_key"].startswith("staging:")


@pytest.mark.asyncio
async def test_text_staging_reserves_global_capacity(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Storage Test",
        slug="storage-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/s.md",
        file_name="s.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()
    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    check_pending = AsyncMock()
    reserve_storage = AsyncMock()
    mock_redis = MagicMock()

    text = "hello world"
    expected_size = len(text.encode("utf-8"))

    with (
        patch("app.routers.materials.read_full_object", new=AsyncMock(return_value=b"old")),
        patch("app.routers.materials.storage_upload_file", new_callable=AsyncMock),
        patch("app.routers.materials._check_pending_cap", check_pending),
        patch("app.routers.materials._reserve_storage_limit", reserve_storage),
    ):
        await save_material_text_content(str(material.id), user, db_session, text, mock_redis)

    reserve_storage.assert_awaited_once()
    # First positional arg is size_bytes
    assert reserve_storage.call_args.args[0] == expected_size


@pytest.mark.asyncio
async def test_text_staging_failure_releases_all_external_state(
    db_session: AsyncSession,
) -> None:
    """When the DB transaction fails after S3 upload, all three external
    resources (object, quota, storage reservation) must be compensated."""
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Rollback All Test",
        slug="rollback-all-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/r.md",
        file_name="r.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()
    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    mock_redis = MagicMock()
    mock_redis.zrem = AsyncMock()
    delete_object = AsyncMock()
    release_reservation = AsyncMock()

    with (
        patch("app.routers.materials.read_full_object", new=AsyncMock(return_value=b"old")),
        patch("app.routers.materials.storage_upload_file", new_callable=AsyncMock),
        patch("app.routers.materials._check_pending_cap", new_callable=AsyncMock),
        patch("app.routers.materials._reserve_storage_limit", new_callable=AsyncMock),
        patch("app.core.storage.facade.delete_object", delete_object),
        patch("app.routers.materials._release_storage_reservation", release_reservation),
    ):
        result = await save_material_text_content(
            str(material.id), user, db_session, "new text", mock_redis
        )

        # Simulate transaction rollback
        await db_session.rollback()
        await rollback_transaction_callbacks(db_session)

    # All three compensations should have executed
    delete_object.assert_awaited_once_with(result["file_key"])
    mock_redis.zrem.assert_awaited()
    release_reservation.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_staging_uses_upload_rate_limit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The text-content save endpoint must enforce rate_limit_uploads."""
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Rate Limit Test",
        slug="rate-limit-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/rl.md",
        file_name="rl.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()

    from app.core.common.exceptions import RateLimitError
    from app.dependencies.rate_limit import rate_limit_uploads
    from app.main import app

    async def _failing_rate_limit():
        raise RateLimitError("Too many uploads")

    app.dependency_overrides[rate_limit_uploads] = _failing_rate_limit
    try:
        import json

        response = await client.post(
            f"/api/materials/{material.id}/text-content",
            content=json.dumps("hello"),
            headers={**_auth_headers(user), "content-type": "application/json"},
        )

    finally:
        app.dependency_overrides.pop(rate_limit_uploads, None)

    assert response.status_code == 429, response.json()


@pytest.mark.asyncio
async def test_text_diff_is_truncated_at_bound(
    db_session: AsyncSession,
) -> None:
    """Diffs larger than _TEXT_DIFF_MAX_BYTES are truncated."""
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Diff Bound Test",
        slug="diff-bound-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/d.md",
        file_name="d.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()
    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    mock_redis = MagicMock()
    # Create a body that is entirely different from old text to maximize diff size.
    old_text = "old\n" * 200_000  # ~800 KB of unique lines
    new_text = "new\n" * 200_000  # ~800 KB of different lines, producing >1 MiB diff

    with (
        patch(
            "app.routers.materials.read_full_object", new=AsyncMock(return_value=old_text.encode())
        ),
        patch("app.routers.materials.storage_upload_file", new_callable=AsyncMock),
        patch("app.routers.materials._check_pending_cap", new_callable=AsyncMock),
        patch("app.routers.materials._reserve_storage_limit", new_callable=AsyncMock),
    ):
        result = await save_material_text_content(
            str(material.id), user, db_session, new_text, mock_redis
        )

    assert "... diff truncated ..." in result["diff"]


@pytest.mark.asyncio
async def test_text_application_mimes_enforce_max_text_size_mb(monkeypatch) -> None:
    """Application text MIMEs (JSON, XML, JS, etc.) must enforce max_text_size_mb."""
    from app.config import settings
    from app.core.common.exceptions import BadRequestError
    from app.core.common.upload_limits import enforce_upload_size_limit, upload_size_limit

    monkeypatch.setattr(settings, "max_text_size_mb", 1)
    monkeypatch.setattr(settings, "max_file_size_mb", 100)

    for mime in (
        "application/json",
        "application/xml",
        "application/javascript",
        "application/typescript",
        "application/x-yaml",
        "application/x-sh",
        "application/sql",
    ):
        limit, is_fallback = upload_size_limit(mime)
        assert limit == 1024 * 1024
        assert is_fallback is False

        # 2 MiB body exceeds 1 MiB max_text_size_mb limit
        with pytest.raises(BadRequestError):
            enforce_upload_size_limit(mime, 2 * 1024 * 1024)


@pytest.mark.asyncio
async def test_legacy_storage_reservation_always_refreshes_from_db(
    db_session: AsyncSession,
) -> None:
    """Legacy storage usage cache in Redis must be overwritten by DB on every reservation."""
    from app.models.material import Material, MaterialVersion
    from app.routers.upload.helpers import (
        _LEGACY_STORAGE_USAGE_KEY,
        _refresh_legacy_storage_usage,
    )
    from tests.test_materials import _create_directory, _create_user

    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Legacy Usage Test",
        slug="legacy-usage-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)

    # 2 MaterialVersions referencing the SAME physical key (10 MiB)
    file_key = "materials/shared/file.md"
    v1 = MaterialVersion(
        material=material,
        version_number=1,
        file_key=file_key,
        file_name="file.md",
        file_size=10 * 1024 * 1024,
        file_mime_type="text/markdown",
    )
    v2 = MaterialVersion(
        material=material,
        version_number=2,
        file_key=file_key,
        file_name="file.md",
        file_size=10 * 1024 * 1024,
        file_mime_type="text/markdown",
    )
    db_session.add(v1)
    db_session.add(v2)
    await db_session.commit()

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock()

    # Redis cache initially claims 0 bytes (stale)
    mock_redis.get = AsyncMock(return_value=b"0")

    # _refresh_legacy_storage_usage must query DB, find 10 MiB (deduplicated), and update Redis
    usage = await _refresh_legacy_storage_usage(db_session, mock_redis)
    assert usage == 10 * 1024 * 1024
    mock_redis.set.assert_awaited_once_with(_LEGACY_STORAGE_USAGE_KEY, 10 * 1024 * 1024)


@pytest.mark.asyncio
async def test_text_staging_lost_success_deletes_object_on_rollback(
    db_session: AsyncSession,
) -> None:
    """When storage_upload_file fails with OSError (acknowledgement lost), rollback compensation deletes object."""
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Lost Success Test",
        slug="lost-success-test",
        type="markdown",
        author_id=user.id,
    )
    db_session.add(material)
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key="materials/test/ls.md",
        file_name="ls.md",
        file_size=3,
        file_mime_type="text/markdown",
    )
    db_session.add(version)
    await db_session.commit()
    db_session.info[PostCommitKey.MANAGED_TRANSACTION] = True

    mock_redis = AsyncMock()
    mock_redis.register_script = MagicMock(return_value=AsyncMock())

    uploaded_keys: set[str] = set()

    async def _lost_success_upload(data, file_key, **kwargs):
        uploaded_keys.add(file_key)
        raise OSError("upload acknowledgement lost")

    async def _delete_object(file_key):
        uploaded_keys.discard(file_key)

    async def _fake_read_full_object(*args, **kwargs) -> bytes:
        return b"old"

    with (
        patch("app.routers.materials.read_full_object", _fake_read_full_object),
        patch("app.routers.materials.storage_upload_file", side_effect=_lost_success_upload),
        patch("app.routers.materials._check_pending_cap", AsyncMock()),
        patch("app.routers.materials._reserve_storage_limit", AsyncMock()),
        patch("app.core.storage.facade.delete_object", side_effect=_delete_object) as delete_mock,
    ):
        with pytest.raises(OSError, match="upload acknowledgement lost"):
            await save_material_text_content(
                str(material.id), user, db_session, "new text", mock_redis
            )

        # Simulate transaction rollback
        await db_session.rollback()
        await rollback_transaction_callbacks(db_session)

    assert len(uploaded_keys) == 0
    delete_mock.assert_awaited()


@pytest.mark.asyncio
async def test_pr_cleanup_releases_storage_reservations_loop(
    db_session: AsyncSession,
    fake_redis_setup,
    monkeypatch,
) -> None:
    """100 stage/cancel cycles release all storage reservations."""
    import uuid

    from app.config import settings
    from app.models.pull_request import PRStatus, PullRequest
    from app.models.upload import Upload
    from app.routers.upload.helpers import _reserve_storage_limit
    from app.services.pr import _cleanup_pr_resources
    from app.workers.storage_ops import delete_storage_objects
    from tests.test_materials import _create_user

    monkeypatch.setattr(settings, "max_storage_gb", 1)
    user = await _create_user(db_session)

    for i in range(100):
        upload_id = str(uuid.uuid4())
        u = Upload(
            upload_id=upload_id,
            user_id=user.id,
            final_key=f"uploads/{user.id}/{upload_id}/doc.txt",
            filename="doc.txt",
            mime_type="text/plain",
            size_bytes=100,
            status="clean",
        )
        db_session.add(u)
        pr = PullRequest(
            author_id=user.id,
            title=f"PR {i}",
            status=PRStatus.REJECTED,
            payload=[{"file_key": u.final_key, "content_sha256": f"sha{i}"}],
        )
        db_session.add(pr)
        await db_session.flush()

        await _reserve_storage_limit(100, upload_id, fake_redis_setup, db_session)
        await _cleanup_pr_resources(
            db_session,
            pr,
            delete_staging=True,
            redis=fake_redis_setup,
        )

    jobs = db_session.info.get(PostCommitKey.JOBS, [])
    delete_jobs = [j for j in jobs if j[0] == "delete_storage_objects"]
    assert len(delete_jobs) == 100
    assert not [j for j in jobs if j[0] == "release_storage_reservations"]
    assert await fake_redis_setup.get("storage:upload_reservations:total") == 10_000

    with patch("app.workers.storage_ops.delete_object", new_callable=AsyncMock):
        for _, keys, reservation_ids in delete_jobs:
            await delete_storage_objects(
                {"redis": fake_redis_setup},
                keys,
                reservation_ids,
            )

    assert await fake_redis_setup.get("storage:upload_reservations:total") == 0


@pytest.mark.asyncio
async def test_pr_cancellation_rollback_keeps_storage_reserved(
    db_session: AsyncSession,
    fake_redis_setup,
    monkeypatch,
) -> None:
    """A rolled-back PR transition must not release its staged object's capacity."""
    import uuid

    import app.core.database.redis as redis_core
    from app.config import settings
    from app.models.pull_request import PRStatus, PullRequest
    from app.models.upload import Upload
    from app.routers.upload.helpers import _reserve_storage_limit
    from app.services.pr import cancel_pr_service
    from tests.test_materials import _create_user

    monkeypatch.setattr(settings, "max_storage_gb", 1)
    monkeypatch.setattr(redis_core, "redis_client", fake_redis_setup)

    user = await _create_user(db_session)
    upload_id = str(uuid.uuid4())
    file_key = f"uploads/{user.id}/{upload_id}/document.txt"
    db_session.add(
        Upload(
            upload_id=upload_id,
            user_id=user.id,
            final_key=file_key,
            filename="document.txt",
            mime_type="text/plain",
            size_bytes=100,
            status="clean",
        )
    )
    pr = PullRequest(
        author_id=user.id,
        title="Cancelled contribution",
        status=PRStatus.OPEN,
        payload=[{"op": "create_material", "file_key": file_key}],
    )
    db_session.add(pr)
    await db_session.commit()

    await _reserve_storage_limit(100, upload_id, fake_redis_setup, db_session)
    quota_key = f"quota:uploads:{user.id}"
    staging_quota_key = f"staging:{user.id}:{upload_id}"
    await fake_redis_setup.zadd(quota_key, {staging_quota_key: 1})
    assert await fake_redis_setup.get("storage:upload_reservations:total") == 100
    assert await fake_redis_setup.zcard(quota_key) == 1

    await cancel_pr_service(db_session, pr.id, user)
    await db_session.rollback()

    assert await fake_redis_setup.get("storage:upload_reservations:total") == 100
    assert await fake_redis_setup.zcard(quota_key) == 1


@pytest.mark.asyncio
async def test_storage_reservation_cleanup_propagates_redis_failures(
    mock_redis,
    monkeypatch,
) -> None:
    """A cleanup worker failure must remain retryable by escaping the worker."""
    from app.config import settings
    from app.workers.storage_ops import release_storage_reservations

    monkeypatch.setattr(settings, "max_storage_gb", 1)
    release_script = AsyncMock(side_effect=ConnectionError("reservation unavailable"))
    mock_redis.register_script.return_value = release_script

    with pytest.raises(ExceptionGroup) as raised:
        await release_storage_reservations(
            {"redis": mock_redis},
            {
                "reservation_ids": ["upload-1"],
                "refresh_legacy_usage": False,
            },
        )

    assert len(raised.value.exceptions) == 1


@pytest.mark.asyncio
async def test_failed_usage_invalidation_keeps_reservation_for_retry(
    db_session: AsyncSession,
    fake_redis_setup,
    monkeypatch,
) -> None:
    """Do not remove the safety reservation while legacy usage may still be stale."""
    from app.config import settings
    from app.core.storage.capacity import reserve_storage_limit
    from app.workers.storage_ops import release_storage_reservations

    monkeypatch.setattr(settings, "max_storage_gb", 1)
    await reserve_storage_limit(100, "upload-1", fake_redis_setup, db_session)
    monkeypatch.setattr(
        fake_redis_setup,
        "delete",
        AsyncMock(side_effect=ConnectionError("cache unavailable")),
    )

    with pytest.raises(ExceptionGroup):
        await release_storage_reservations(
            {"redis": fake_redis_setup},
            {
                "reservation_ids": ["upload-1"],
                "refresh_legacy_usage": True,
            },
        )

    assert await fake_redis_setup.get("storage:upload_reservations:total") == 100


@pytest.mark.asyncio
async def test_failed_staging_deletion_keeps_reservation_for_retry(
    db_session: AsyncSession,
    fake_redis_setup,
    monkeypatch,
) -> None:
    """A rejected staging object remains accounted for until deletion succeeds."""
    from app.config import settings
    from app.core.storage.capacity import reserve_storage_limit
    from app.workers.storage_ops import delete_storage_objects

    monkeypatch.setattr(settings, "max_storage_gb", 1)
    await reserve_storage_limit(100, "upload-1", fake_redis_setup, db_session)

    with (
        patch("app.workers.storage_ops.delete_object", side_effect=OSError("S3 unavailable")),
        pytest.raises(ExceptionGroup),
    ):
        await delete_storage_objects(
            {"redis": fake_redis_setup},
            ["uploads/user/upload-1/document.txt"],
            ["upload-1"],
        )

    assert await fake_redis_setup.get("storage:upload_reservations:total") == 100

    with patch("app.workers.storage_ops.delete_object", new_callable=AsyncMock):
        await delete_storage_objects(
            {"redis": fake_redis_setup},
            ["uploads/user/upload-1/document.txt"],
            ["upload-1"],
        )

    assert await fake_redis_setup.get("storage:upload_reservations:total") == 0
