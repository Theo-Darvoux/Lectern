"""
Tests for the GDPR cleanup worker.

Bug fixed: gdpr_cleanup's User SELECT was silently returning 0 rows because
database.py registers a global soft-delete filter (do_orm_execute event) that
adds `User.deleted_at IS NULL` to all User queries.  The fix adds
`.execution_options(include_deleted=True)` to bypass the filter.

Testing strategy:
- Selection logic: patch hard_delete_user to capture which users were selected.
  (The internal session created by gdpr_cleanup uses async_session_factory, which
  the conftest patches to use the shared SQLite connection.  We avoid committing
  that session by stubbing out hard_delete_user.)
- hard_delete_user correctness: tested directly via db_session.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.workers.gdpr_cleanup import GDPR_RETENTION_DAYS, gdpr_cleanup


def _past(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def _make_user(db: AsyncSession, deleted_at: datetime | None = None) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@test.example",
        display_name="Test",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
        deleted_at=deleted_at,
    )
    db.add(user)
    await db.flush()
    return user


async def test_gdpr_cleanup_selects_past_retention(db_session: AsyncSession) -> None:
    old = await _make_user(db_session, deleted_at=_past(GDPR_RETENTION_DAYS + 1))

    deleted_ids: list[uuid.UUID] = []

    async def capture(db: AsyncSession, user: User) -> None:
        deleted_ids.append(user.id)

    with patch("app.workers.gdpr_cleanup.hard_delete_user", side_effect=capture):
        await gdpr_cleanup({})

    assert old.id in deleted_ids


async def test_gdpr_cleanup_skips_recently_soft_deleted(db_session: AsyncSession) -> None:
    recent = await _make_user(db_session, deleted_at=_past(GDPR_RETENTION_DAYS - 1))

    deleted_ids: list[uuid.UUID] = []

    async def capture(db: AsyncSession, user: User) -> None:
        deleted_ids.append(user.id)

    with patch("app.workers.gdpr_cleanup.hard_delete_user", side_effect=capture):
        await gdpr_cleanup({})

    assert recent.id not in deleted_ids


async def test_gdpr_cleanup_skips_active_users(db_session: AsyncSession) -> None:
    active = await _make_user(db_session, deleted_at=None)

    deleted_ids: list[uuid.UUID] = []

    async def capture(db: AsyncSession, user: User) -> None:
        deleted_ids.append(user.id)

    with patch("app.workers.gdpr_cleanup.hard_delete_user", side_effect=capture):
        await gdpr_cleanup({})

    assert active.id not in deleted_ids


async def test_gdpr_cleanup_noop_when_no_eligible_users(db_session: AsyncSession) -> None:
    await _make_user(db_session, deleted_at=None)

    mock_hard_delete = AsyncMock()
    with patch("app.workers.gdpr_cleanup.hard_delete_user", mock_hard_delete):
        await gdpr_cleanup({})

    mock_hard_delete.assert_not_called()


async def test_gdpr_cleanup_handles_exactly_at_cutoff(db_session: AsyncSession) -> None:
    """User deleted exactly GDPR_RETENTION_DAYS ago is at/past the cutoff and must be purged."""
    boundary = await _make_user(db_session, deleted_at=_past(GDPR_RETENTION_DAYS))

    deleted_ids: list[uuid.UUID] = []

    async def capture(db: AsyncSession, user: User) -> None:
        deleted_ids.append(user.id)

    with patch("app.workers.gdpr_cleanup.hard_delete_user", side_effect=capture):
        await gdpr_cleanup({})

    assert boundary.id in deleted_ids


async def test_hard_delete_user_removes_user_row(db_session: AsyncSession) -> None:
    """hard_delete_user must delete the user from the DB."""
    from app.services.user import hard_delete_user

    user = await _make_user(db_session)
    await db_session.flush()

    with patch("app.services.user.delete_object", new_callable=AsyncMock):
        await hard_delete_user(db_session, user)
    await db_session.flush()

    result = (
        await db_session.execute(
            select(User).where(User.id == user.id).execution_options(include_deleted=True)
        )
    ).scalar_one_or_none()
    assert result is None
