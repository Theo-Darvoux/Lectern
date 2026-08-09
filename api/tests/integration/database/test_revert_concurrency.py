from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.common.exceptions import BadRequestError
from app.core.database.post_commit import PostCommitKey, persist_post_commit_jobs
from app.models.dead_letter import DeadLetterJob
from app.models.directory import Directory, DirectoryType
from app.models.outbox import OutboxJob
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.routers.admin import retry_dead_letter_job
from app.services.pr import _exec_move_item, revert_pr_service
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
            select(func.count()).select_from(OutboxJob).where(
                OutboxJob.job_name == "process_upload",
                OutboxJob.args
                == [{"__outbox_kwargs__": {"upload_id": retried_upload_id}}],
            )
        )
        assert count == 1

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
