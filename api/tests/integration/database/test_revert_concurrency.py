from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.common.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.database.post_commit import PostCommitKey, persist_post_commit_jobs
from app.models.dead_letter import DeadLetterJob
from app.models.directory import Directory, DirectoryType
from app.models.material import Material
from app.models.outbox import OutboxJob
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.routers.admin import retry_dead_letter_job
from app.schemas.pull_request import PullRequestCreate
from app.services.pr import (
    _exec_create_directory,
    _exec_create_material,
    _exec_delete_directory,
    _exec_delete_material,
    _exec_move_item,
    apply_pr,
    create_pull_request_service,
    revert_pr_service,
)
from app.workers.year_rollover import year_rollover

DATABASE_URL = os.environ.get("REVERT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="REVERT_TEST_DATABASE_URL is not configured"),
]


async def _seed_original(session_factory: async_sessionmaker) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_factory() as session:
        admin = User(
            email=f"revert-race-{uuid.uuid4()}@example.invalid",
            display_name="Revert race admin",
            role=UserRole.BUREAU,
        )
        session.add(admin)
        await session.flush()
        original = PullRequest(
            type="batch",
            status=PRStatus.APPROVED,
            title="Original contribution",
            payload=[],
            summary_types=[],
            author_id=admin.id,
            reviewed_by=admin.id,
            approved_at=datetime.now(UTC),
            applied_result=[],
        )
        session.add(original)
        await session.commit()
        return original.id, admin.id


@pytest.mark.asyncio
async def test_simultaneous_reverts_serialize_on_original_row() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original_id, admin_id = await _seed_original(sessions)
    actor = User(id=admin_id, email="detached@example.invalid", role=UserRole.BUREAU)

    async with sessions() as first, sessions() as second:
        stale_original = await second.get(PullRequest, original_id)
        assert stale_original is not None
        assert stale_original.reverted_by_pr_id is None

        first_revert = await revert_pr_service(first, original_id, actor)
        second_task = asyncio.create_task(revert_pr_service(second, original_id, actor))
        await asyncio.sleep(0.2)
        assert not second_task.done(), "second transaction did not wait for the row lock"

        await first.commit()
        with pytest.raises(BadRequestError, match="already been reverted"):
            await asyncio.wait_for(second_task, timeout=5)
        await second.rollback()

        async with sessions() as check:
            count = await check.scalar(
                select(func.count())
                .select_from(PullRequest)
                .where(PullRequest.reverts_pr_id == original_id)
            )
            original = await check.get(PullRequest, original_id)
            assert count == 1
            assert original is not None
            assert original.reverted_by_pr_id == first_revert.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_constraint_rejects_duplicate_revert_reference() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original_id, admin_id = await _seed_original(sessions)

    async with sessions() as session:
        for suffix in ("one", "two"):
            session.add(
                PullRequest(
                    type="revert",
                    status=PRStatus.APPROVED,
                    title=f"Duplicate revert {suffix}",
                    payload=[],
                    summary_types=[],
                    author_id=admin_id,
                    reviewed_by=admin_id,
                    approved_at=datetime.now(UTC),
                    applied_result=[],
                    reverts_pr_id=original_id,
                )
            )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_year_rollover_has_one_durable_winner() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_year = 3000 + uuid.uuid4().int % 1000

    async with sessions() as seed:
        user = User(
            email=f"rollover-race-{uuid.uuid4()}@example.invalid",
            display_name="Rollover race user",
            role=UserRole.STUDENT,
            academic_year="1A",
        )
        seed.add(user)
        await seed.commit()
        user_id = user.id

    await asyncio.gather(
        year_rollover({"db_sessionmaker": sessions}, target_year=run_year),
        year_rollover({"db_sessionmaker": sessions}, target_year=run_year),
    )

    async with sessions() as check:
        updated = await check.get(User, user_id)
        assert updated is not None
        assert updated.academic_year == "2A"

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_dead_letter_retries_enqueue_exactly_once() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    admin = User(id=uuid.uuid4(), email="detached@example.invalid", role=UserRole.BUREAU)

    retried_upload_id = str(uuid.uuid4())
    async with sessions() as seed:
        job = DeadLetterJob(
            job_name="process_upload",
            upload_id=str(uuid.uuid4()),
            payload={"upload_id": retried_upload_id},
            error_detail="failed",
            attempts=3,
        )
        seed.add(job)
        await seed.commit()
        job_id = job.id

    async with sessions() as first, sessions() as second:
        await first.scalar(
            select(DeadLetterJob).where(DeadLetterJob.id == job_id).with_for_update()
        )
        second.info[PostCommitKey.JOBS] = []
        competing = asyncio.create_task(retry_dead_letter_job(job_id, admin, second))
        await asyncio.sleep(0.2)
        assert not competing.done(), "competing retry did not wait for the DLQ row lock"

        first.info[PostCommitKey.JOBS] = []
        await retry_dead_letter_job(job_id, admin, first)
        await persist_post_commit_jobs(first)
        await first.commit()

        with pytest.raises(BadRequestError, match="already been resolved"):
            await asyncio.wait_for(competing, timeout=5)
        await second.rollback()

    async with sessions() as check:
        count = await check.scalar(
            select(func.count())
            .select_from(OutboxJob)
            .where(
                OutboxJob.job_name == "process_upload",
                OutboxJob.args == [{"__outbox_kwargs__": {"upload_id": retried_upload_id}}],
            )
        )
        assert count == 1
        # This integration module shares one real PostgreSQL database with the
        # other concurrency tests in the CI job. Do not leave a due outbox row
        # behind for a later test of the global dispatcher to claim.
        await check.execute(
            delete(OutboxJob).where(
                OutboxJob.job_name == "process_upload",
                OutboxJob.args == [{"__outbox_kwargs__": {"upload_id": retried_upload_id}}],
            )
        )
        await check.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_directory_moves_cannot_create_a_cycle() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original_id, _ = await _seed_original(sessions)

    async with sessions() as seed:
        first_dir = Directory(name="First", slug=f"first-{uuid.uuid4()}", type=DirectoryType.FOLDER)
        second_dir = Directory(
            name="Second", slug=f"second-{uuid.uuid4()}", type=DirectoryType.FOLDER
        )
        seed.add_all([first_dir, second_dir])
        await seed.commit()
        first_id, second_id = first_dir.id, second_dir.id

    async with sessions() as first, sessions() as second:
        first_pr = await first.get(PullRequest, original_id)
        second_pr = await second.get(PullRequest, original_id)
        assert first_pr is not None and second_pr is not None

        await _exec_move_item(
            first,
            {
                "target_type": "directory",
                "target_id": str(first_id),
                "new_parent_id": str(second_id),
            },
            first_pr,
            {},
        )
        competing_move = asyncio.create_task(
            _exec_move_item(
                second,
                {
                    "target_type": "directory",
                    "target_id": str(second_id),
                    "new_parent_id": str(first_id),
                },
                second_pr,
                {},
            )
        )
        await asyncio.sleep(0.2)
        assert not competing_move.done(), "competing move did not wait for the tree lock"

        await first.commit()
        with pytest.raises(BadRequestError, match="own descendants"):
            await asyncio.wait_for(competing_move, timeout=5)
        await second.rollback()

    async with sessions() as check:
        first_row = await check.get(Directory, first_id)
        second_row = await check.get(Directory, second_id)
        assert first_row is not None and second_row is not None
        assert first_row.parent_id == second_id
        assert second_row.parent_id is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_root_directory_slug_unique_during_concurrent_insert() -> None:
    """The DB constraint itself closes the NULL-parent uniqueness hole."""
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    slug = f"raw-root-race-{uuid.uuid4().hex[:12]}"

    async with sessions() as first, sessions() as second:
        first.add(Directory(name="First root", slug=slug, type=DirectoryType.FOLDER))
        second.add(Directory(name="Second root", slug=slug, type=DirectoryType.FOLDER))

        await first.flush()
        competing_flush = asyncio.create_task(second.flush())
        await asyncio.sleep(0.2)
        assert not competing_flush.done(), "duplicate insert did not wait on the unique index"

        await first.commit()
        with pytest.raises(IntegrityError):
            await asyncio.wait_for(competing_flush, timeout=5)
        await second.rollback()

    async with sessions() as check:
        count = await check.scalar(
            select(func.count())
            .select_from(Directory)
            .where(Directory.parent_id.is_(None), Directory.slug == slug)
        )
        assert count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_root_directory_creates_allocate_distinct_slugs() -> None:
    """Application allocation serializes even when the requested slug has no row yet."""
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original_id, _ = await _seed_original(sessions)
    name = f"root-race-{uuid.uuid4().hex[:12]}"

    async with sessions() as first, sessions() as second:
        first_pr = await first.get(PullRequest, original_id)
        second_pr = await second.get(PullRequest, original_id)
        assert first_pr is not None and second_pr is not None

        first_id = await _exec_create_directory(first, {"name": name}, first_pr, {})
        competing_create = asyncio.create_task(
            _exec_create_directory(second, {"name": name}, second_pr, {})
        )
        await asyncio.sleep(0.2)
        assert not competing_create.done(), "competing create did not wait for the namespace lock"

        await first.commit()
        second_id = await asyncio.wait_for(competing_create, timeout=5)
        await second.commit()

    async with sessions() as check:
        rows = list(
            (
                await check.scalars(
                    select(Directory).where(Directory.id.in_([first_id, second_id]))
                )
            ).all()
        )
        assert len(rows) == 2
        assert all(row.parent_id is None for row in rows)
        assert {row.slug for row in rows} == {name, f"{name}-2"}

    await engine.dispose()


@pytest.mark.asyncio
async def test_root_directory_revert_rejects_reused_slug_without_partial_restore() -> None:
    """delete A -> create B with A's slug -> revert A is conflict-safe."""
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original_id, admin_id = await _seed_original(sessions)
    actor = User(id=admin_id, email="detached@example.invalid", role=UserRole.BUREAU)
    name = f"restore-root-{uuid.uuid4().hex[:12]}"

    async with sessions() as seed:
        original = Directory(name=name, slug=name, type=DirectoryType.FOLDER)
        seed.add(original)
        await seed.commit()
        original_dir_id = original.id

    async with sessions() as delete_session:
        pr = await delete_session.get(PullRequest, original_id)
        assert pr is not None
        await _exec_delete_directory(delete_session, {"directory_id": str(original_dir_id)}, pr, {})
        # Make this approved PR accurately describe the deletion so the public
        # revert service builds an undelete_directory reverse operation.
        pr.payload = [{"op": "delete_directory", "directory_id": str(original_dir_id)}]
        pr.applied_result = [{"op": "delete_directory", "result_id": str(original_dir_id)}]
        pr.summary_types = ["delete_directory"]
        await delete_session.commit()

    async with sessions() as create_session:
        pr = await create_session.get(PullRequest, original_id)
        assert pr is not None
        replacement_id = await _exec_create_directory(create_session, {"name": name}, pr, {})
        await create_session.commit()

    async with sessions() as restore_session:
        with pytest.raises(ConflictError, match="already in use"):
            await revert_pr_service(restore_session, original_id, actor)
        await restore_session.rollback()

    async with sessions() as check:
        original_row = await check.scalar(
            select(Directory)
            .where(Directory.id == original_dir_id)
            .execution_options(include_deleted=True)
        )
        replacement = await check.get(Directory, replacement_id)
        original_pr = await check.get(PullRequest, original_id)
        revert_count = await check.scalar(
            select(func.count())
            .select_from(PullRequest)
            .where(PullRequest.reverts_pr_id == original_id)
        )
        assert original_row is not None and original_row.deleted_at is not None
        assert replacement is not None and replacement.deleted_at is None
        assert replacement.parent_id is None
        assert replacement.slug == name
        assert original_pr is not None and original_pr.reverted_by_pr_id is None
        assert revert_count == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_directory_delete_serializes_against_child_creation() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original_id, _ = await _seed_original(sessions)

    async with sessions() as seed:
        parent = Directory(
            name="Concurrent parent",
            slug=f"concurrent-parent-{uuid.uuid4().hex[:12]}",
            type=DirectoryType.FOLDER,
        )
        seed.add(parent)
        await seed.commit()
        parent_id = parent.id

    async with sessions() as deleting, sessions() as creating:
        delete_pr = await deleting.get(PullRequest, original_id)
        create_pr = await creating.get(PullRequest, original_id)
        assert delete_pr is not None and create_pr is not None

        await _exec_delete_directory(deleting, {"directory_id": str(parent_id)}, delete_pr, {})
        competing = asyncio.create_task(
            _exec_create_directory(
                creating,
                {"name": "Impossible child", "parent_id": str(parent_id)},
                create_pr,
                {},
            )
        )
        await asyncio.sleep(0.2)
        assert not competing.done(), "child creation did not wait for the hierarchy lock"

        await deleting.commit()
        with pytest.raises(NotFoundError, match="Parent directory not found"):
            await asyncio.wait_for(competing, timeout=5)
        await creating.rollback()

    async with sessions() as check:
        live_children = await check.scalar(
            select(func.count())
            .select_from(Directory)
            .where(Directory.parent_id == parent_id, Directory.deleted_at.is_(None))
        )
        assert live_children == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_material_delete_serializes_against_attachment_creation() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original_id, _ = await _seed_original(sessions)

    async with sessions() as seed:
        parent = Material(
            title="Concurrent material parent",
            slug=f"concurrent-material-parent-{uuid.uuid4().hex[:12]}",
            type="document",
        )
        seed.add(parent)
        await seed.commit()
        parent_id = parent.id

    async with sessions() as deleting, sessions() as creating:
        delete_pr = await deleting.get(PullRequest, original_id)
        create_pr = await creating.get(PullRequest, original_id)
        assert delete_pr is not None and create_pr is not None

        await _exec_delete_material(deleting, {"material_id": str(parent_id)}, delete_pr, {})
        competing = asyncio.create_task(
            _exec_create_material(
                creating,
                {
                    "title": "Impossible attachment",
                    "type": "document",
                    "parent_material_id": str(parent_id),
                },
                create_pr,
                {},
            )
        )
        await asyncio.sleep(0.2)
        assert not competing.done(), "attachment creation did not wait for the hierarchy lock"

        await deleting.commit()
        with pytest.raises(NotFoundError, match="Parent material not found"):
            await asyncio.wait_for(competing, timeout=5)
        await creating.rollback()

    async with sessions() as check:
        live_attachments = await check.scalar(
            select(func.count())
            .select_from(Material)
            .where(Material.parent_material_id == parent_id, Material.deleted_at.is_(None))
        )
        assert live_attachments == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_pr_creation_cannot_exceed_per_user_open_limit() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as seed:
        user = User(
            email=f"pr-cap-{uuid.uuid4()}@example.invalid",
            display_name="PR cap user",
            role=UserRole.STUDENT,
        )
        seed.add(user)
        await seed.flush()
        for index in range(4):
            seed.add(
                PullRequest(
                    type="batch",
                    status=PRStatus.OPEN,
                    title=f"Existing {index}",
                    payload=[],
                    summary_types=[],
                    author_id=user.id,
                )
            )
        await seed.commit()
        user_id = user.id

    first_data = PullRequestCreate(
        title="Concurrent contribution A",
        operations=[{"op": "create_directory", "temp_id": "$a", "name": "A"}],
    )
    second_data = PullRequestCreate(
        title="Concurrent contribution B",
        operations=[{"op": "create_directory", "temp_id": "$b", "name": "B"}],
    )
    detached_user = User(
        id=user_id,
        email="detached-pr-cap@example.invalid",
        role=UserRole.STUDENT,
    )

    async with sessions() as first, sessions() as second:
        await create_pull_request_service(first, first_data, detached_user)
        competing = asyncio.create_task(
            create_pull_request_service(second, second_data, detached_user)
        )
        await asyncio.sleep(0.2)
        assert not competing.done(), "competing PR admission did not wait for the user lock"

        await first.commit()
        with pytest.raises(BadRequestError, match="contributions pending review"):
            await asyncio.wait_for(competing, timeout=5)
        await second.rollback()

    async with sessions() as check:
        open_count = await check.scalar(
            select(func.count())
            .select_from(PullRequest)
            .where(
                PullRequest.author_id == user_id,
                PullRequest.status == PRStatus.OPEN,
            )
        )
        assert open_count == 5

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_moves_capture_authoritative_pre_state_for_revert() -> None:
    """The second move must snapshot B after the first A->B move commits."""
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as seed:
        admin = User(
            email=f"move-prestate-{uuid.uuid4()}@example.invalid",
            display_name="Move pre-state admin",
            role=UserRole.BUREAU,
        )
        directory_a = Directory(
            name="Move A",
            slug=f"move-a-{uuid.uuid4().hex[:12]}",
            type=DirectoryType.FOLDER,
        )
        directory_b = Directory(
            name="Move B",
            slug=f"move-b-{uuid.uuid4().hex[:12]}",
            type=DirectoryType.FOLDER,
        )
        directory_c = Directory(
            name="Move C",
            slug=f"move-c-{uuid.uuid4().hex[:12]}",
            type=DirectoryType.FOLDER,
        )
        seed.add_all([admin, directory_a, directory_b, directory_c])
        await seed.flush()

        material = Material(
            title="Concurrent move target",
            slug=f"concurrent-move-{uuid.uuid4().hex[:12]}",
            type="document",
            directory_id=directory_a.id,
            author_id=admin.id,
        )
        seed.add(material)
        await seed.flush()

        first_pr = PullRequest(
            type="batch",
            status=PRStatus.APPROVED,
            title="Move target to B",
            payload=[
                {
                    "op": "move_item",
                    "target_type": "material",
                    "target_id": str(material.id),
                    "new_parent_id": str(directory_b.id),
                }
            ],
            summary_types=["move_item"],
            author_id=admin.id,
            reviewed_by=admin.id,
        )
        second_pr = PullRequest(
            type="batch",
            status=PRStatus.APPROVED,
            title="Move target to C",
            payload=[
                {
                    "op": "move_item",
                    "target_type": "material",
                    "target_id": str(material.id),
                    "new_parent_id": str(directory_c.id),
                }
            ],
            summary_types=["move_item"],
            author_id=admin.id,
            reviewed_by=admin.id,
        )
        seed.add_all([first_pr, second_pr])
        await seed.commit()

        admin_id = admin.id
        material_id = material.id
        directory_b_id = directory_b.id
        directory_c_id = directory_c.id
        first_pr_id = first_pr.id
        second_pr_id = second_pr.id

    async with sessions() as first, sessions() as second:
        pr_one = await first.get(PullRequest, first_pr_id)
        pr_two = await second.get(PullRequest, second_pr_id)
        assert pr_one is not None and pr_two is not None

        await apply_pr(first, pr_one)
        second_apply = asyncio.create_task(apply_pr(second, pr_two))
        await asyncio.sleep(0.2)
        assert not second_apply.done(), "second move did not wait for the hierarchy transition"

        await first.commit()
        await asyncio.wait_for(second_apply, timeout=5)

        assert pr_two.applied_result is not None
        assert pr_two.applied_result[0]["pre_state"]["prev_directory_id"] == str(directory_b_id)
        await second.commit()

    async with sessions() as check:
        moved = await check.get(Material, material_id)
        assert moved is not None
        assert moved.directory_id == directory_c_id

    actor = User(id=admin_id, email="detached-move-prestate@example.invalid", role=UserRole.BUREAU)
    async with sessions() as reverting:
        await revert_pr_service(reverting, second_pr_id, actor)
        await reverting.commit()

    async with sessions() as check:
        reverted = await check.get(Material, material_id)
        assert reverted is not None
        assert reverted.directory_id == directory_b_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_mixed_pr_takes_hierarchy_lock_before_early_row_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mixed PR must not invert target-row and hierarchy advisory lock order."""
    assert DATABASE_URL is not None
    from app.services import pr as pr_service

    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as seed:
        admin = User(
            email=f"mixed-lock-order-{uuid.uuid4()}@example.invalid",
            display_name="Mixed lock-order admin",
            role=UserRole.BUREAU,
        )
        target = Directory(
            name="Mixed lock target",
            slug=f"mixed-lock-target-{uuid.uuid4().hex[:12]}",
            type=DirectoryType.FOLDER,
            description="before",
        )
        destination = Directory(
            name="Mixed lock destination",
            slug=f"mixed-lock-destination-{uuid.uuid4().hex[:12]}",
            type=DirectoryType.FOLDER,
        )
        seed.add_all([admin, target, destination])
        await seed.flush()

        mixed_pr = PullRequest(
            type="batch",
            status=PRStatus.APPROVED,
            title="Edit then allocate namespace",
            payload=[
                {
                    "op": "edit_directory",
                    "directory_id": str(target.id),
                    "description": "after",
                },
                {
                    "op": "create_directory",
                    "temp_id": "$created",
                    "name": "Created after edit",
                },
            ],
            summary_types=["edit_directory", "create_directory"],
            author_id=admin.id,
            reviewed_by=admin.id,
        )
        move_pr = PullRequest(
            type="batch",
            status=PRStatus.APPROVED,
            title="Move same directory",
            payload=[
                {
                    "op": "move_item",
                    "target_type": "directory",
                    "target_id": str(target.id),
                    "new_parent_id": str(destination.id),
                }
            ],
            summary_types=["move_item"],
            author_id=admin.id,
            reviewed_by=admin.id,
        )
        seed.add_all([mixed_pr, move_pr])
        await seed.commit()

        target_id = target.id
        destination_id = destination.id
        mixed_pr_id = mixed_pr.id
        move_pr_id = move_pr.id

    row_locked = asyncio.Event()
    release_first = asyncio.Event()
    original_capture = pr_service._capture_pre_state

    async def capture_and_pause(
        db,
        op_type: str,
        op: dict,
        id_map: dict,  # type: ignore[no-untyped-def]
    ):
        pre_state = await original_capture(db, op_type, op, id_map)
        if op_type == "edit_directory" and op.get("description") == "after":
            row_locked.set()
            await release_first.wait()
        return pre_state

    monkeypatch.setattr(pr_service, "_capture_pre_state", capture_and_pause)

    async with sessions() as first, sessions() as second:
        first_pr = await first.get(PullRequest, mixed_pr_id)
        second_pr = await second.get(PullRequest, move_pr_id)
        assert first_pr is not None and second_pr is not None

        first_apply = asyncio.create_task(apply_pr(first, first_pr))
        await asyncio.wait_for(row_locked.wait(), timeout=5)

        second_apply = asyncio.create_task(apply_pr(second, second_pr))
        await asyncio.sleep(0.2)
        assert not second_apply.done(), "competing move did not wait for the PR-wide hierarchy lock"

        release_first.set()
        await asyncio.wait_for(first_apply, timeout=5)
        await first.commit()

        await asyncio.wait_for(second_apply, timeout=5)
        await second.commit()

    async with sessions() as check:
        moved = await check.get(Directory, target_id)
        assert moved is not None
        assert moved.description == "after"
        assert moved.parent_id == destination_id
        created_count = await check.scalar(
            select(func.count())
            .select_from(Directory)
            .where(Directory.name == "Created after edit", Directory.deleted_at.is_(None))
        )
        assert created_count == 1

    await engine.dispose()
