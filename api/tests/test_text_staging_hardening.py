import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.upload_errors import UploadErrorCode
from app.core.database.post_commit import PostCommitKey, rollback_transaction_callbacks
from app.models.material import Material, MaterialVersion
from app.routers.materials import (
    _TEXT_EDIT_MAX_BYTES,
    _TEXT_DIFF_MAX_BYTES,
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
    client: AsyncClient, db_session: AsyncSession, monkeypatch,
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
        await save_material_text_content(
            str(material.id), user, db_session, "new text", mock_redis
        )

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
        await save_material_text_content(
            str(material.id), user, db_session, text, mock_redis
        )

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
    client: AsyncClient, db_session: AsyncSession,
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
        patch("app.routers.materials.read_full_object", new=AsyncMock(return_value=old_text.encode())),
        patch("app.routers.materials.storage_upload_file", new_callable=AsyncMock),
        patch("app.routers.materials._check_pending_cap", new_callable=AsyncMock),
        patch("app.routers.materials._reserve_storage_limit", new_callable=AsyncMock),
    ):
        result = await save_material_text_content(
            str(material.id), user, db_session, new_text, mock_redis
        )

    assert "... diff truncated ..." in result["diff"]

