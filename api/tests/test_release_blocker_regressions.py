from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers.materials as materials_router
import app.services.pr as pr_service
import app.workers.cleanup_uploads as cleanup_module
from app.config import settings
from app.core.common.exceptions import ConflictError
from app.core.database.post_commit import PostCommitKey
from app.core.storage import capacity
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PullRequest
from app.models.upload import Upload
from tests.test_materials import _create_directory, _create_user


@pytest.mark.asyncio
async def test_cas_upload_missing_authoritative_mime_fails_closed(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    key = f"cas/{uuid.uuid4().hex}"
    db_session.add(
        Upload(
            upload_id=str(uuid.uuid4()),
            user_id=user.id,
            final_key=key,
            filename="file.bin",
            mime_type=None,
            size_bytes=100,
            content_sha256="a" * 64,
            status="clean",
            cas_ref_count=1,
        )
    )
    await db_session.flush()

    with pytest.raises(ConflictError, match="metadata is incomplete"):
        await pr_service._make_version_for_file(
            db_session,
            file_key=key,
            payload={"file_mime_type": "text/plain", "content_sha256": "a" * 64},
            material_id=uuid.uuid4(),
            version_number=1,
            author_id=user.id,
            pr_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_cas_upload_missing_authoritative_hash_fails_closed(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    key = f"cas/{uuid.uuid4().hex}"
    db_session.add(
        Upload(
            upload_id=str(uuid.uuid4()),
            user_id=user.id,
            final_key=key,
            filename="file.txt",
            mime_type="text/plain",
            size_bytes=100,
            content_sha256=None,
            sha256="b" * 64,
            status="clean",
            cas_ref_count=1,
        )
    )
    await db_session.flush()

    with pytest.raises(ConflictError, match="metadata is incomplete"):
        await pr_service._make_version_for_file(
            db_session,
            file_key=key,
            payload={"file_mime_type": "text/plain", "content_sha256": "c" * 64},
            material_id=uuid.uuid4(),
            version_number=1,
            author_id=user.id,
            pr_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_legacy_material_version_never_gets_payload_cas_hash(
    db_session: AsyncSession, monkeypatch
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Legacy metadata regression",
        slug="legacy-metadata-regression",
        type="document",
        author_id=user.id,
    )
    pr = PullRequest(title="Legacy metadata regression", payload=[], author_id=user.id)
    db_session.add_all([material, pr])
    await db_session.flush()

    monkeypatch.setattr(
        pr_service,
        "get_object_info",
        AsyncMock(return_value={"size": 100, "content_type": "text/plain"}),
    )
    monkeypatch.setattr(pr_service, "copy_object", AsyncMock())
    mv = await pr_service._make_version_for_file(
        db_session,
        file_key=f"uploads/{user.id}/{uuid.uuid4()}/file.txt",
        payload={
            "file_name": "file.txt",
            "file_mime_type": "text/plain",
            "content_sha256": "d" * 64,
        },
        material_id=material.id,
        version_number=1,
        author_id=user.id,
        pr_id=pr.id,
    )
    assert mv.cas_sha256 is None


@pytest.mark.asyncio
async def test_legacy_usage_counts_soft_deleted_version_inside_revert_grace(
    db_session: AsyncSession, fake_redis_setup, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "pr_revert_grace_days", 7)
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Revertable",
        slug="revertable",
        type="document",
        author_id=user.id,
    )
    db_session.add(material)
    db_session.add(
        MaterialVersion(
            material=material,
            version_number=1,
            file_key="materials/revertable/file.txt",
            file_name="file.txt",
            file_size=123,
            file_mime_type="text/plain",
            deleted_at=datetime.now(UTC),
        )
    )
    await db_session.flush()
    assert await capacity.refresh_legacy_storage_usage(db_session, fake_redis_setup) == 123


@pytest.mark.asyncio
async def test_promoted_release_fences_stale_reservation_snapshot(
    db_session: AsyncSession, fake_redis_setup, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "max_storage_gb", 1)
    await capacity.reserve_storage_limit(100, "promoted", fake_redis_setup, db_session)

    snapshot_read = asyncio.Event()
    allow_reserve = asyncio.Event()
    original_usage = capacity._legacy_storage_usage_from_database
    calls = 0

    async def controlled_usage(db):
        nonlocal calls
        calls += 1
        result = await original_usage(db)
        if calls == 1:
            snapshot_read.set()
            await allow_reserve.wait()
        return result

    monkeypatch.setattr(capacity, "_legacy_storage_usage_from_database", controlled_usage)

    task = asyncio.create_task(
        capacity.reserve_storage_limit(50, "new", fake_redis_setup, db_session)
    )
    await snapshot_read.wait()
    await capacity.release_promoted_legacy_storage_reservation("promoted", fake_redis_setup)
    allow_reserve.set()
    await task

    assert calls >= 2
    assert int(await fake_redis_setup.get(capacity.LEGACY_STORAGE_GENERATION_KEY)) == 1


class _NoSplitStr(str):
    def splitlines(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("splitlines() must not run for oversized diff input")


def test_text_diff_preflight_skips_split_and_difflib_for_many_lines() -> None:
    hostile = _NoSplitStr("x\n" * (materials_router._TEXT_DIFF_MAX_LINES + 1))
    with patch.object(
        materials_router.difflib,
        "unified_diff",
        side_effect=AssertionError("difflib must not run"),
    ):
        diff = materials_router._build_bounded_text_diff(
            hostile,
            hostile,
            old_size_bytes=len(hostile.encode()),
            new_size_bytes=len(hostile.encode()),
            fromfile="old.txt",
            tofile="new.txt",
        )
    assert materials_router._TEXT_DIFF_OMITTED in diff


def test_text_diff_preflight_skips_difflib_for_large_bytes() -> None:
    text = _NoSplitStr("x" * (materials_router._TEXT_DIFF_INPUT_MAX_BYTES + 1))
    with patch.object(
        materials_router.difflib,
        "unified_diff",
        side_effect=AssertionError("difflib must not run"),
    ):
        diff = materials_router._build_bounded_text_diff(
            text,
            "small",
            old_size_bytes=len(text),
            new_size_bytes=5,
            fromfile="old.txt",
            tofile="new.txt",
        )
    assert materials_router._TEXT_DIFF_OMITTED in diff


def test_text_diff_small_inputs_still_produce_unified_diff() -> None:
    diff = materials_router._build_bounded_text_diff(
        "old\n",
        "new\n",
        old_size_bytes=4,
        new_size_bytes=4,
        fromfile="old.txt",
        tofile="new.txt",
    )
    assert "--- old.txt" in diff
    assert "+++ new.txt" in diff
    assert "-old" in diff
    assert "+new" in diff


@pytest.mark.asyncio
async def test_expired_legacy_bytes_remain_charged_until_delete_succeeds(
    db_session: AsyncSession,
    fake_redis_setup,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "pr_revert_grace_days", 7)
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Expired legacy capacity",
        slug="expired-legacy-capacity",
        type="document",
        author_id=user.id,
    )
    key = "materials/expired/file.txt"
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key=key,
        file_name="file.txt",
        file_size=321,
        file_mime_type="text/plain",
        deleted_at=datetime.now(UTC) - timedelta(days=8),
    )
    db_session.add_all([material, version])
    await db_session.commit()

    assert await capacity._legacy_storage_usage_from_database(db_session) == 321
    cutoff = datetime.now(UTC) - timedelta(days=7)

    with patch(
        "app.workers.storage_ops.delete_storage_objects",
        new=AsyncMock(side_effect=OSError("S3 unavailable")),
    ):
        with pytest.raises(OSError, match="S3 unavailable"):
            await cleanup_module._reap_expired_legacy_material_key(
                db_session,
                {"redis": fake_redis_setup},
                key,
                cutoff,
                object_seen=True,
            )
    await db_session.rollback()
    assert await capacity._legacy_storage_usage_from_database(db_session) == 321

    delete_objects = AsyncMock()
    with patch("app.workers.storage_ops.delete_storage_objects", new=delete_objects):
        assert await cleanup_module._reap_expired_legacy_material_key(
            db_session,
            {"redis": fake_redis_setup},
            key,
            cutoff,
            object_seen=True,
        )
    delete_objects.assert_awaited_once_with({"redis": fake_redis_setup}, [key])
    assert await capacity._legacy_storage_usage_from_database(db_session) == 0


@pytest.mark.asyncio
async def test_pr_cleanup_releases_cas_ref_without_payload_hash(
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = await _create_user(db_session)
    key = f"cas/{uuid.uuid4().hex}"
    sha256 = "e" * 64
    upload = Upload(
        upload_id=str(uuid.uuid4()),
        user_id=user.id,
        final_key=key,
        filename="file.txt",
        mime_type="text/plain",
        size_bytes=100,
        content_sha256=sha256,
        status="clean",
        cas_ref_count=1,
    )
    pr = PullRequest(
        title="CAS cleanup without client hash",
        author_id=user.id,
        payload=[{"op": "create_material", "file_key": key}],
    )
    db_session.add_all([upload, pr])
    await db_session.flush()
    db_session.info[PostCommitKey.JOBS] = []

    await pr_service._cleanup_pr_resources(db_session, pr, redis=fake_redis_setup)

    assert upload.cas_ref_count == 0
    release_job = next(
        job for job in db_session.info[PostCommitKey.JOBS] if job[0] == "release_cas_references"
    )
    assert release_job[1] == [
        {
            "sha256": sha256,
            "operation_id": f"pr:{pr.id}:staging:{sha256}:release",
        }
    ]


@pytest.mark.asyncio
async def test_expired_soft_deleted_cas_version_releases_ref_and_is_reaped(
    db_session: AsyncSession,
    fake_redis_setup,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "pr_revert_grace_days", 7)
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="Expired CAS owner",
        slug="expired-cas-owner",
        type="document",
        author_id=user.id,
    )
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key=f"cas/{uuid.uuid4().hex}",
        file_name="file.txt",
        file_size=100,
        file_mime_type="text/plain",
        cas_sha256="f" * 64,
        deleted_at=datetime.now(UTC) - timedelta(days=8),
    )
    db_session.add_all([material, version])
    await db_session.commit()

    decrement = AsyncMock(return_value=0)
    cutoff = datetime.now(UTC) - timedelta(days=7)
    with patch("app.core.security.cas.decrement_cas_ref", new=decrement):
        released = await cleanup_module._release_expired_cas_material_versions(
            db_session, fake_redis_setup, cutoff
        )
        assert released == 1
        await db_session.commit()

    decrement.assert_awaited_once_with(
        fake_redis_setup,
        "f" * 64,
        operation_id=f"cleanup:material-version:{version.id}:expire",
    )
    remaining = await db_session.scalar(
        select(MaterialVersion)
        .where(MaterialVersion.id == version.id)
        .execution_options(include_deleted=True)
    )
    assert remaining is None


@pytest.mark.asyncio
async def test_expired_cas_version_is_not_reaped_when_ref_release_fails(
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = Material(
        directory_id=directory.id,
        title="CAS release failure",
        slug="cas-release-failure",
        type="document",
        author_id=user.id,
    )
    version = MaterialVersion(
        material=material,
        version_number=1,
        file_key=f"cas/{uuid.uuid4().hex}",
        file_name="file.txt",
        file_size=100,
        file_mime_type="text/plain",
        cas_sha256="a" * 64,
        deleted_at=datetime.now(UTC) - timedelta(days=settings.pr_revert_grace_days + 1),
    )
    db_session.add_all([material, version])
    await db_session.commit()
    version_id = version.id

    cutoff = datetime.now(UTC) - timedelta(days=settings.pr_revert_grace_days)
    with patch(
        "app.core.security.cas.decrement_cas_ref",
        new=AsyncMock(side_effect=ConnectionError("Redis unavailable")),
    ):
        released = await cleanup_module._release_expired_cas_material_versions(
            db_session, fake_redis_setup, cutoff
        )
    assert released == 0
    await db_session.rollback()
    remaining = await db_session.scalar(
        select(MaterialVersion)
        .where(MaterialVersion.id == version_id)
        .execution_options(include_deleted=True)
    )
    assert remaining is not None
