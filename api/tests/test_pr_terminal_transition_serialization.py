from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User
from app.services import pr as pr_service


class _SharedState:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.author_id = uuid.uuid4()
        self.status = PRStatus.OPEN
        self.lock = asyncio.Lock()


class _TransitionSession:
    """Model PostgreSQL row-lock behavior independently of SQLite."""

    def __init__(self, shared: _SharedState) -> None:
        self.shared = shared
        self.info: dict[str, Any] = {}
        self.local: SimpleNamespace | None = None
        self.holds_lock = False
        self.saw_populate_existing = False

    async def scalar(self, statement: Any) -> SimpleNamespace:
        assert getattr(statement, "_for_update_arg", None) is not None
        await self.shared.lock.acquire()
        self.holds_lock = True
        self.saw_populate_existing = bool(
            statement.get_execution_options().get("populate_existing")
        )
        self.local = SimpleNamespace(
            id=self.shared.id,
            author_id=self.shared.author_id,
            status=self.shared.status,
            title="Concurrent contribution",
            payload=[],
            reviewed_by=None,
            rejection_reason=None,
            applied_result=None,
        )
        return self.local

    async def commit(self) -> None:
        assert self.local is not None
        self.shared.status = self.local.status
        self._release()

    async def rollback(self) -> None:
        self._release()

    def _release(self) -> None:
        if self.holds_lock:
            self.shared.lock.release()
            self.holds_lock = False


async def _commit_service(session: _TransitionSession, awaitable: Any) -> Any:
    try:
        result = await awaitable
        await session.commit()
        return result
    except BaseException:
        await session.rollback()
        raise


async def _close_service(
    mode: str,
    session: _TransitionSession,
    shared: _SharedState,
) -> Any:
    actor = cast(User, SimpleNamespace(id=shared.author_id))
    if mode == "reject":
        return await pr_service.reject_pr_service(
            cast(AsyncSession, session), shared.id, "Rejected", actor
        )
    return await pr_service.cancel_pr_service(cast(AsyncSession, session), shared.id, actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("losing_close", ["reject", "cancel"])
async def test_approval_winner_prevents_stale_close(losing_close: str) -> None:
    shared = _SharedState()
    approval_db = _TransitionSession(shared)
    close_db = _TransitionSession(shared)
    reviewer = cast(User, SimpleNamespace(id=uuid.uuid4()))
    apply_entered = asyncio.Event()
    release_apply = asyncio.Event()

    async def blocked_apply(_db: AsyncSession, _pr: PullRequest) -> None:
        apply_entered.set()
        await release_apply.wait()

    with (
        patch.object(pr_service, "_lock_and_validate_pr_cas_files", new=AsyncMock()),
        patch.object(pr_service, "apply_pr", new=AsyncMock(side_effect=blocked_apply)),
        patch.object(pr_service, "_cleanup_pr_resources", new=AsyncMock()),
        patch.object(pr_service, "notify_user", new=AsyncMock()),
    ):
        approval = asyncio.create_task(
            _commit_service(
                approval_db,
                pr_service.approve_pr_service(cast(AsyncSession, approval_db), shared.id, reviewer),
            )
        )
        await asyncio.wait_for(apply_entered.wait(), timeout=2)
        close = asyncio.create_task(
            _commit_service(close_db, _close_service(losing_close, close_db, shared))
        )
        await asyncio.sleep(0)
        assert not close.done()
        release_apply.set()
        await approval
        with pytest.raises(BadRequestError, match="no longer open"):
            await close

    assert shared.status == PRStatus.APPROVED
    assert approval_db.saw_populate_existing
    assert close_db.saw_populate_existing


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("winning_close", "expected"),
    [("reject", PRStatus.REJECTED), ("cancel", PRStatus.CANCELLED)],
)
async def test_close_winner_prevents_stale_approval(
    winning_close: str,
    expected: PRStatus,
) -> None:
    shared = _SharedState()
    close_db = _TransitionSession(shared)
    approval_db = _TransitionSession(shared)
    reviewer = cast(User, SimpleNamespace(id=uuid.uuid4()))
    cleanup_entered = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def blocked_cleanup(*_args: Any, **_kwargs: Any) -> None:
        cleanup_entered.set()
        await release_cleanup.wait()

    with (
        patch.object(
            pr_service,
            "_cleanup_pr_resources",
            new=AsyncMock(side_effect=blocked_cleanup),
        ),
        patch.object(pr_service, "notify_user", new=AsyncMock()),
        patch.object(pr_service, "_lock_and_validate_pr_cas_files", new=AsyncMock()),
        patch.object(pr_service, "apply_pr", new=AsyncMock()),
    ):
        close = asyncio.create_task(
            _commit_service(close_db, _close_service(winning_close, close_db, shared))
        )
        await asyncio.wait_for(cleanup_entered.wait(), timeout=2)
        approval = asyncio.create_task(
            _commit_service(
                approval_db,
                pr_service.approve_pr_service(cast(AsyncSession, approval_db), shared.id, reviewer),
            )
        )
        await asyncio.sleep(0)
        assert not approval.done()
        release_cleanup.set()
        await close
        with pytest.raises(BadRequestError, match="no longer open"):
            await approval

    assert shared.status == expected
    assert close_db.saw_populate_existing
    assert approval_db.saw_populate_existing
