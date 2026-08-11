from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database.post_commit import acknowledge_outbox_completion, dispatch_pending_outbox
from app.models.outbox import OutboxJob

DATABASE_URL = os.environ.get("REVERT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="REVERT_TEST_DATABASE_URL is not configured"),
]


@pytest.mark.asyncio
async def test_concurrent_deindex_dispatch_has_one_lease_and_requires_ack() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = f"deindex-pg-{uuid.uuid4()}"

    # dispatch_pending_outbox() is intentionally global. This test is about two
    # dispatchers racing for one specific row, so establish that one-row
    # precondition explicitly instead of depending on which integration test ran
    # before it in the shared PostgreSQL service.
    async with sessions() as isolate:
        await isolate.execute(delete(OutboxJob))
        await isolate.commit()

    async with sessions() as seed:
        row = OutboxJob(
            job_name="delete_indexed_item",
            args=["materials", marker],
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        seed.add(row)
        await seed.commit()
        row_id = row.id

    pool = AsyncMock()

    async def deliver() -> int:
        async with sessions() as session:
            return await dispatch_pending_outbox(session, limit=1)

    with patch("app.core.database.redis.arq_pool", pool):
        results = await asyncio.gather(deliver(), deliver())

    assert sum(results) == 1
    pool.enqueue_job.assert_awaited_once()
    assert pool.enqueue_job.await_args.kwargs["outbox_id"] == str(row_id)

    async with sessions() as check:
        persisted = await check.get(OutboxJob, row_id)
        assert persisted is not None
        assert persisted.delivered_at is not None
        assert persisted.completed_at is None

    assert await acknowledge_outbox_completion(sessions, str(row_id)) is True

    async with sessions() as check:
        persisted = await check.get(OutboxJob, row_id)
        assert persisted is not None
        assert persisted.completed_at is not None
        await check.execute(delete(OutboxJob).where(OutboxJob.id == row_id))
        await check.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_restored_live_material_supersedes_old_deindex() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.models.material import Material
    from app.workers.index_content import delete_indexed_item

    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex

    async with sessions() as seed:
        material = Material(
            title="Restored live material",
            slug=f"restored-live-{marker}",
            type="file",
        )
        seed.add(material)
        await seed.flush()
        row = OutboxJob(
            job_name="delete_indexed_item",
            args=["materials", str(material.id)],
        )
        seed.add(row)
        await seed.commit()
        material_id = material.id
        row_id = row.id

    index = MagicMock()
    index.delete_document = AsyncMock(return_value=SimpleNamespace(task_uid=71))
    with patch("app.workers.index_content.meili_admin_client.index", return_value=index):
        await delete_indexed_item(
            {"db_sessionmaker": sessions},
            "materials",
            str(material_id),
            outbox_id=str(row_id),
        )

    index.delete_document.assert_not_awaited()
    async with sessions() as check:
        persisted = await check.get(OutboxJob, row_id)
        assert persisted is not None and persisted.completed_at is not None
        await check.execute(delete(OutboxJob).where(OutboxJob.id == row_id))
        await check.execute(delete(Material).where(Material.id == material_id))
        await check.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_deindex_row_lock_serializes_concurrent_restore() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from sqlalchemy import update

    from app.models.material import Material
    from app.workers.index_content import delete_indexed_item

    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex

    async with sessions() as seed:
        material = Material(
            title="Deleted then restored material",
            slug=f"restore-race-{marker}",
            type="file",
            deleted_at=datetime.now(UTC),
        )
        seed.add(material)
        await seed.flush()
        row = OutboxJob(
            job_name="delete_indexed_item",
            args=["materials", str(material.id)],
        )
        seed.add(row)
        await seed.commit()
        material_id = material.id
        row_id = row.id

    remote_started = asyncio.Event()
    release_remote = asyncio.Event()
    index = MagicMock()

    async def delete_remote(_item_id: str):
        remote_started.set()
        await release_remote.wait()
        return SimpleNamespace(task_uid=72)

    index.delete_document = AsyncMock(side_effect=delete_remote)
    wait_for_task = AsyncMock(return_value=SimpleNamespace(status="succeeded"))

    async def restore() -> None:
        async with sessions() as db:
            await db.execute(
                update(Material)
                .where(Material.id == material_id)
                .values(deleted_at=None)
                .execution_options(include_deleted=True)
            )
            await db.commit()

    with (
        patch("app.workers.index_content.meili_admin_client.index", return_value=index),
        patch("app.workers.index_content.meili_admin_client.wait_for_task", wait_for_task),
    ):
        worker = asyncio.create_task(
            delete_indexed_item(
                {"db_sessionmaker": sessions},
                "materials",
                str(material_id),
                outbox_id=str(row_id),
            )
        )
        await asyncio.wait_for(remote_started.wait(), timeout=5)
        restore_task = asyncio.create_task(restore())
        await asyncio.sleep(0.1)
        assert not restore_task.done(), "restore should wait for the deindex target row lock"
        release_remote.set()
        await asyncio.wait_for(worker, timeout=5)
        await asyncio.wait_for(restore_task, timeout=5)

    async with sessions() as check:
        restored = await check.scalar(
            select(Material)
            .where(Material.id == material_id)
            .execution_options(include_deleted=True)
        )
        persisted = await check.get(OutboxJob, row_id)
        assert restored is not None and restored.deleted_at is None
        assert persisted is not None and persisted.completed_at is not None
        await check.execute(delete(OutboxJob).where(OutboxJob.id == row_id))
        await check.execute(delete(Material).where(Material.id == material_id))
        await check.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciler_candidate_restored_material_is_not_deleted() -> None:
    """Restore after orphan discovery but before delete makes the candidate obsolete."""
    from unittest.mock import MagicMock

    from sqlalchemy import update

    from app.models.material import Material
    from app.workers.index_content import _reconcile_search_index

    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex

    async with sessions() as seed:
        material = Material(
            title="Reconcile restore material",
            slug=f"reconcile-restore-{marker}",
            type="file",
            deleted_at=datetime.now(UTC),
        )
        seed.add(material)
        await seed.commit()
        material_id = material.id

    candidate_ready = asyncio.Event()
    continue_delete = asyncio.Event()
    documents = {str(material_id)}

    async def stale_candidates(*_args, **_kwargs):
        candidate_ready.set()
        await continue_delete.wait()
        return [str(material_id)]

    index = MagicMock()

    async def delete_documents(ids: list[str]):
        documents.difference_update(ids)
        return type("Task", (), {"task_uid": 81})()

    index.delete_documents = AsyncMock(side_effect=delete_documents)

    with (
        patch(
            "app.workers.index_content._collect_orphan_search_ids",
            side_effect=stale_candidates,
        ),
        patch("app.workers.index_content.meili_admin_client.index", return_value=index),
    ):
        reconcile = asyncio.create_task(_reconcile_search_index(sessions, "materials", Material))
        await asyncio.wait_for(candidate_ready.wait(), timeout=5)

        async with sessions() as restore:
            await restore.execute(
                update(Material)
                .where(Material.id == material_id)
                .values(deleted_at=None)
                .execution_options(include_deleted=True)
            )
            await restore.commit()
        # Model the restore's later index_material success before the stale
        # reconciliation candidate is allowed to continue.
        documents.add(str(material_id))

        continue_delete.set()
        removed = await asyncio.wait_for(reconcile, timeout=5)

    assert removed == 0
    assert str(material_id) in documents
    index.delete_documents.assert_not_awaited()

    async with sessions() as cleanup:
        await cleanup.execute(delete(Material).where(Material.id == material_id))
        await cleanup.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciler_candidate_restored_directory_subtree_is_not_deleted() -> None:
    """A stale candidate list cannot erase a restored directory subtree."""
    from unittest.mock import MagicMock

    from sqlalchemy import update

    from app.models.directory import Directory, DirectoryType
    from app.workers.index_content import _reconcile_search_index

    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    deleted_at = datetime.now(UTC)

    async with sessions() as seed:
        parent = Directory(
            name="Reconcile restore parent",
            slug=f"reconcile-parent-{marker}",
            type=DirectoryType.FOLDER,
            deleted_at=deleted_at,
        )
        child = Directory(
            name="Reconcile restore child",
            slug=f"reconcile-child-{marker}",
            type=DirectoryType.FOLDER,
            parent_id=parent.id,
            deleted_at=deleted_at,
        )
        seed.add_all([parent, child])
        await seed.commit()
        directory_ids = [parent.id, child.id]

    candidate_ready = asyncio.Event()
    continue_delete = asyncio.Event()
    documents = {str(item_id) for item_id in directory_ids}

    async def stale_candidates(*_args, **_kwargs):
        candidate_ready.set()
        await continue_delete.wait()
        return [str(item_id) for item_id in directory_ids]

    index = MagicMock()

    async def delete_documents(ids: list[str]):
        documents.difference_update(ids)
        return type("Task", (), {"task_uid": 82})()

    index.delete_documents = AsyncMock(side_effect=delete_documents)

    with (
        patch(
            "app.workers.index_content._collect_orphan_search_ids",
            side_effect=stale_candidates,
        ),
        patch("app.workers.index_content.meili_admin_client.index", return_value=index),
    ):
        reconcile = asyncio.create_task(_reconcile_search_index(sessions, "directories", Directory))
        await asyncio.wait_for(candidate_ready.wait(), timeout=5)

        async with sessions() as restore:
            await restore.execute(
                update(Directory)
                .where(Directory.id.in_(directory_ids))
                .values(deleted_at=None)
                .execution_options(include_deleted=True)
            )
            await restore.commit()
        documents.update(str(item_id) for item_id in directory_ids)

        continue_delete.set()
        removed = await asyncio.wait_for(reconcile, timeout=5)

    assert removed == 0
    assert documents == {str(item_id) for item_id in directory_ids}
    index.delete_documents.assert_not_awaited()

    async with sessions() as cleanup:
        await cleanup.execute(delete(Directory).where(Directory.id.in_(directory_ids)))
        await cleanup.commit()
    await engine.dispose()
