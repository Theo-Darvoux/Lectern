from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.database as db_core
from app.core.database.post_commit import dispatch_pending_outbox
from app.models.directory import Directory, DirectoryType
from app.models.material import Material
from app.models.outbox import OutboxJob
from app.services.search import perform_search
from app.workers.index_content import (
    _reconcile_search_index,
    delete_indexed_item,
    reconcile_search_documents,
)
from app.workers.outbox import dispatch_outbox


@pytest.mark.asyncio
async def test_deindex_outbox_remains_pending_until_worker_ack(db_session: AsyncSession) -> None:
    row = OutboxJob(
        job_name="delete_indexed_item",
        args=["materials", "material-id"],
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(row)
    await db_session.commit()

    pool = AsyncMock()
    with patch("app.core.database.redis.arq_pool", pool):
        assert await dispatch_pending_outbox(db_session) == 1

    await db_session.refresh(row)
    assert row.delivered_at is not None
    assert row.completed_at is None
    assert row.attempts == 1
    assert pool.enqueue_job.await_args.args == (
        "delete_indexed_item",
        "materials",
        "material-id",
    )
    assert pool.enqueue_job.await_args.kwargs["outbox_id"] == str(row.id)
    assert pool.enqueue_job.await_args.kwargs["_job_id"] == f"outbox:{row.id}:attempt:1"

    # Expired lease => a new idempotent delivery attempt, not a terminal row.
    row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    with patch("app.core.database.redis.arq_pool", pool):
        assert await dispatch_pending_outbox(db_session) == 1
    assert pool.enqueue_job.await_count == 2
    assert pool.enqueue_job.await_args.kwargs["_job_id"] == f"outbox:{row.id}:attempt:2"


@pytest.mark.asyncio
async def test_deindex_enqueue_failures_are_never_abandoned(db_session: AsyncSession) -> None:
    row = OutboxJob(
        job_name="delete_indexed_item",
        args=["directories", "directory-id"],
        attempts=9,
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(row)
    await db_session.commit()

    pool = AsyncMock()
    pool.enqueue_job.side_effect = OSError("redis unavailable")
    with patch("app.core.database.redis.arq_pool", pool):
        assert await dispatch_pending_outbox(db_session) == 0

    await db_session.refresh(row)
    assert row.attempts == 10
    assert row.abandoned_at is None
    assert row.completed_at is None
    assert row.last_error == "redis unavailable"


@pytest.mark.asyncio
async def test_legacy_abandoned_deindex_is_reopened_and_retried(
    db_session: AsyncSession,
) -> None:
    row = OutboxJob(
        job_name="delete_indexed_item",
        args=["materials", "legacy-stale"],
        attempts=10,
        abandoned_at=datetime.now(UTC) - timedelta(days=40),
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(row)
    await db_session.commit()

    pool = AsyncMock()
    with patch("app.core.database.redis.arq_pool", pool):
        assert await dispatch_pending_outbox(db_session) == 1

    await db_session.refresh(row)
    assert row.abandoned_at is None
    assert row.completed_at is None
    assert row.attempts == 11
    assert pool.enqueue_job.await_args.kwargs["outbox_id"] == str(row.id)


@pytest.mark.asyncio
async def test_deindex_worker_acks_only_after_meili_task_succeeds(db_session: AsyncSession) -> None:
    row = OutboxJob(job_name="delete_indexed_item", args=["materials", "material-id"])
    db_session.add(row)
    await db_session.commit()

    index = MagicMock()
    index.delete_document = AsyncMock(return_value=SimpleNamespace(task_uid=42))
    wait_for_task = AsyncMock(return_value=SimpleNamespace(status="succeeded"))
    with (
        patch("app.workers.index_content.meili_admin_client.index", return_value=index),
        patch("app.workers.index_content.meili_admin_client.wait_for_task", wait_for_task),
    ):
        await delete_indexed_item(
            {"db_sessionmaker": db_core.async_session_factory},
            "materials",
            "material-id",
            outbox_id=str(row.id),
        )

    wait_for_task.assert_awaited_once_with(42, timeout_in_ms=30_000, raise_for_status=True)
    await db_session.refresh(row)
    assert row.completed_at is not None
    assert row.last_error is None


@pytest.mark.asyncio
async def test_obsolete_material_deindex_cannot_delete_restored_live_document(
    db_session: AsyncSession,
) -> None:
    material = Material(
        title="Restored material",
        slug=f"restored-material-{uuid.uuid4().hex}",
        type="file",
        deleted_at=None,
    )
    outbox = OutboxJob(
        job_name="delete_indexed_item",
        args=["materials", str(material.id)],
    )
    db_session.add_all([material, outbox])
    await db_session.commit()

    index = MagicMock()
    index.delete_document = AsyncMock()
    with patch("app.workers.index_content.meili_admin_client.index", return_value=index):
        await delete_indexed_item(
            {"db_sessionmaker": db_core.async_session_factory},
            "materials",
            str(material.id),
            outbox_id=str(outbox.id),
        )

    index.delete_document.assert_not_awaited()
    await db_session.refresh(outbox)
    assert outbox.completed_at is not None
    assert (await db_session.get(Material, material.id)) is not None


@pytest.mark.asyncio
async def test_obsolete_directory_deindex_cannot_delete_restored_live_subtree(
    db_session: AsyncSession,
) -> None:
    parent = Directory(
        name="Restored parent",
        slug=f"restored-parent-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
        deleted_at=None,
    )
    child = Directory(
        name="Restored child",
        slug=f"restored-child-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
        parent_id=parent.id,
        deleted_at=None,
    )
    parent_job = OutboxJob(
        job_name="delete_indexed_item",
        args=["directories", str(parent.id)],
    )
    child_job = OutboxJob(
        job_name="delete_indexed_item",
        args=["directories", str(child.id)],
    )
    db_session.add_all([parent, child, parent_job, child_job])
    await db_session.commit()

    index = MagicMock()
    index.delete_document = AsyncMock()
    with patch("app.workers.index_content.meili_admin_client.index", return_value=index):
        for target, job in ((parent, parent_job), (child, child_job)):
            await delete_indexed_item(
                {"db_sessionmaker": db_core.async_session_factory},
                "directories",
                str(target.id),
                outbox_id=str(job.id),
            )

    index.delete_document.assert_not_awaited()
    await db_session.refresh(parent_job)
    await db_session.refresh(child_job)
    assert parent_job.completed_at is not None
    assert child_job.completed_at is not None


@pytest.mark.asyncio
async def test_deindex_worker_failure_stays_durable(db_session: AsyncSession) -> None:
    row = OutboxJob(job_name="delete_indexed_item", args=["materials", "material-id"])
    db_session.add(row)
    await db_session.commit()

    index = MagicMock()
    index.delete_document = AsyncMock(return_value=SimpleNamespace(task_uid=43))
    wait_for_task = AsyncMock(side_effect=OSError("meili task failed"))
    with (
        patch("app.workers.index_content.meili_admin_client.index", return_value=index),
        patch("app.workers.index_content.meili_admin_client.wait_for_task", wait_for_task),
    ):
        with pytest.raises(OSError, match="meili task failed"):
            await delete_indexed_item(
                {"db_sessionmaker": db_core.async_session_factory},
                "materials",
                "material-id",
                outbox_id=str(row.id),
            )

    await db_session.refresh(row)
    assert row.completed_at is None
    assert row.last_error == "meili task failed"
    assert row.next_attempt_at is not None


@pytest.mark.asyncio
async def test_outbox_cleanup_keeps_unacknowledged_deindex(db_session: AsyncSession) -> None:
    old = datetime.now(UTC) - timedelta(days=8)
    unacked = OutboxJob(
        job_name="delete_indexed_item",
        args=["materials", "still-pending"],
        delivered_at=old,
    )
    acked = OutboxJob(
        job_name="delete_indexed_item",
        args=["materials", "done"],
        delivered_at=old,
        completed_at=old,
    )
    legacy_abandoned = OutboxJob(
        job_name="delete_indexed_item",
        args=["materials", "legacy-abandoned"],
        abandoned_at=old,
        next_attempt_at=old,
    )
    ordinary = OutboxJob(job_name="index_material", args=["ordinary"], delivered_at=old)
    db_session.add_all([unacked, acked, legacy_abandoned, ordinary])
    await db_session.commit()
    unacked_id = unacked.id
    legacy_abandoned_id = legacy_abandoned.id

    with patch("app.workers.outbox.dispatch_pending_outbox", AsyncMock(return_value=0)):
        await dispatch_outbox({"db_sessionmaker": db_core.async_session_factory})

    remaining = set(await db_session.scalars(select(OutboxJob.id)))
    assert remaining == {unacked_id, legacy_abandoned_id}


@pytest.mark.asyncio
async def test_reconciler_repairs_soft_deleted_search_documents(db_session: AsyncSession) -> None:
    deleted_at = datetime.now(UTC)
    deleted_directory = Directory(
        name="Deleted directory",
        slug=f"deleted-directory-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
        deleted_at=deleted_at,
    )
    deleted_material = Material(
        title="Deleted material",
        slug=f"deleted-material-{uuid.uuid4().hex}",
        type="file",
        deleted_at=deleted_at,
    )
    live_material = Material(
        title="Live material",
        slug=f"live-material-{uuid.uuid4().hex}",
        type="file",
    )
    db_session.add_all([deleted_directory, deleted_material, live_material])
    await db_session.commit()

    missing_material_id = uuid.uuid4()
    materials_index = MagicMock()
    materials_index.get_documents = AsyncMock(
        return_value=SimpleNamespace(
            results=[
                {"id": str(deleted_material.id)},
                {"id": str(live_material.id)},
                {"id": str(missing_material_id)},
                {"id": "malformed-stale-id"},
            ]
        )
    )
    materials_index.delete_documents = AsyncMock(return_value=SimpleNamespace(task_uid=51))
    directories_index = MagicMock()
    directories_index.get_documents = AsyncMock(
        return_value=SimpleNamespace(results=[{"id": str(deleted_directory.id)}])
    )
    directories_index.delete_documents = AsyncMock(return_value=SimpleNamespace(task_uid=52))

    def index_for(name: str):
        return {"materials": materials_index, "directories": directories_index}[name]

    with (
        patch("app.workers.index_content.meili_admin_client.index", side_effect=index_for),
        patch(
            "app.workers.index_content.meili_admin_client.wait_for_task",
            AsyncMock(return_value=SimpleNamespace(status="succeeded")),
        ),
    ):
        await reconcile_search_documents({"db_sessionmaker": db_core.async_session_factory})

    materials_index.delete_documents.assert_awaited_once_with(
        [str(deleted_material.id), str(missing_material_id), "malformed-stale-id"]
    )
    directories_index.delete_documents.assert_awaited_once_with([str(deleted_directory.id)])
    assert str(live_material.id) not in materials_index.delete_documents.await_args.args[0]


@pytest.mark.asyncio
async def test_reconciler_rechecks_material_candidate_after_stale_scan(
    db_session: AsyncSession,
) -> None:
    """A candidate discovered while deleted is skipped if it is live at deletion time."""
    material = Material(
        title="Restored during reconciliation",
        slug=f"reconcile-restored-{uuid.uuid4().hex}",
        type="file",
        deleted_at=None,
    )
    db_session.add(material)
    await db_session.commit()

    index = MagicMock()
    index.delete_documents = AsyncMock()
    with (
        patch(
            "app.workers.index_content._collect_orphan_search_ids",
            AsyncMock(return_value=[str(material.id)]),
        ),
        patch("app.workers.index_content.meili_admin_client.index", return_value=index),
    ):
        removed = await _reconcile_search_index(
            db_core.async_session_factory,
            "materials",
            Material,
        )

    assert removed == 0
    index.delete_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_rechecks_restored_directory_subtree_candidates(
    db_session: AsyncSession,
) -> None:
    parent = Directory(
        name="Restored reconcile parent",
        slug=f"reconcile-parent-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
        deleted_at=None,
    )
    child = Directory(
        name="Restored reconcile child",
        slug=f"reconcile-child-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
        parent_id=parent.id,
        deleted_at=None,
    )
    db_session.add_all([parent, child])
    await db_session.commit()

    index = MagicMock()
    index.delete_documents = AsyncMock()
    with (
        patch(
            "app.workers.index_content._collect_orphan_search_ids",
            AsyncMock(return_value=[str(parent.id), str(child.id)]),
        ),
        patch("app.workers.index_content.meili_admin_client.index", return_value=index),
    ):
        removed = await _reconcile_search_index(
            db_core.async_session_factory,
            "directories",
            Directory,
        )

    assert removed == 0
    index.delete_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_never_returns_deleted_missing_or_malformed_meili_hits(
    db_session: AsyncSession,
) -> None:
    deleted_at = datetime.now(UTC)
    live_material = Material(
        title="Live material",
        slug=f"live-search-{uuid.uuid4().hex}",
        type="file",
    )
    deleted_material = Material(
        title="Deleted secret material",
        slug=f"deleted-search-{uuid.uuid4().hex}",
        type="file",
        deleted_at=deleted_at,
    )
    live_directory = Directory(
        name="Live directory",
        slug=f"live-directory-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
    )
    deleted_directory = Directory(
        name="Deleted secret directory",
        slug=f"deleted-directory-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
        deleted_at=deleted_at,
    )
    db_session.add_all([live_material, deleted_material, live_directory, deleted_directory])
    await db_session.commit()

    missing_material_id = uuid.uuid4()
    missing_directory_id = uuid.uuid4()
    client = AsyncMock()
    client.multi_search.return_value = [
        SimpleNamespace(
            hits=[
                {"id": str(live_material.id), "title": "Live material"},
                {"id": str(deleted_material.id), "title": "Deleted secret material"},
                {"id": str(missing_material_id), "title": "Hard-deleted secret material"},
                {"id": "not-a-uuid", "title": "Malformed stale material"},
            ],
            estimated_total_hits=4,
        ),
        SimpleNamespace(
            hits=[
                {"id": str(live_directory.id), "name": "Live directory"},
                {"id": str(deleted_directory.id), "name": "Deleted secret directory"},
                {"id": str(missing_directory_id), "name": "Hard-deleted secret directory"},
            ],
            estimated_total_hits=3,
        ),
    ]

    with patch("app.services.search.get_search_client", return_value=client):
        result = await perform_search(db_session, "secret")

    assert [item["id"] for item in result["items"]] == [
        str(live_material.id),
        str(live_directory.id),
    ]
    rendered = repr(result["items"])
    assert "Deleted secret" not in rendered
    assert "Hard-deleted secret" not in rendered
    assert "Malformed stale" not in rendered
    # Neither fields nor client-visible counts disclose stale matches.
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_search_filters_stale_hits_before_pagination_and_count(
    db_session: AsyncSession,
) -> None:
    live = [
        Material(
            title=f"Live {i}",
            slug=f"live-pagination-{i}-{uuid.uuid4().hex}",
            type="file",
        )
        for i in range(3)
    ]
    stale = Material(
        title="Deleted secret",
        slug=f"stale-pagination-{uuid.uuid4().hex}",
        type="file",
        deleted_at=datetime.now(UTC),
    )
    db_session.add_all([*live, stale])
    await db_session.commit()

    missing_id = uuid.uuid4()
    raw_hits = [
        {"id": str(stale.id), "title": "Deleted secret"},
        {"id": str(missing_id), "title": "Hard-deleted secret"},
        {"id": str(live[0].id), "title": "Live 0"},
        {"id": str(live[1].id), "title": "Live 1"},
        {"id": str(live[2].id), "title": "Live 2"},
    ]
    client = AsyncMock()
    client.multi_search.return_value = [
        SimpleNamespace(hits=raw_hits, estimated_total_hits=99),
        SimpleNamespace(hits=[], estimated_total_hits=0),
    ]

    with patch("app.services.search.get_search_client", return_value=client):
        page1 = await perform_search(db_session, "secret", page=1, limit=2)
        page2 = await perform_search(db_session, "secret", page=2, limit=2)

    assert page1["total"] == 3
    assert [item["title"] for item in page1["items"]] == ["Live 0", "Live 1"]
    assert page2["total"] == 3
    assert [item["title"] for item in page2["items"]] == ["Live 2"]
    assert "secret" not in repr(page1["items"]).lower()
    assert "secret" not in repr(page2["items"]).lower()


def test_search_scan_bound_matches_explicit_meili_pagination_horizon() -> None:
    from app.core.events.meilisearch import SEARCH_MAX_TOTAL_HITS
    from app.services.search import _SEARCH_SCAN_MAX_HITS_PER_INDEX

    assert _SEARCH_SCAN_MAX_HITS_PER_INDEX == SEARCH_MAX_TOTAL_HITS == 1_000


@pytest.mark.asyncio
async def test_search_too_broad_never_returns_unvalidated_meili_total(
    db_session: AsyncSession,
) -> None:
    from app.core.common.exceptions import BadRequestError

    client = AsyncMock()
    client.multi_search.return_value = [
        SimpleNamespace(hits=[], estimated_total_hits=1_001),
        SimpleNamespace(hits=[], estimated_total_hits=0),
    ]

    with patch("app.services.search.get_search_client", return_value=client):
        with pytest.raises(BadRequestError, match="too broad"):
            await perform_search(db_session, "broad")
