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
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.services.pr import revert_pr_service

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
