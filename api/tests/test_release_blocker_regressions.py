from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.pr as pr_service
from app.config import settings
from app.core.common.exceptions import ConflictError
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
