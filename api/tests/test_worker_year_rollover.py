"""
Data-behavior tests for the year_rollover worker.

year_rollover advances User.academic_year according to ROLLOVER_MAP:
  1A → 2A → 3A+ → 3A+ (capped)
Users with no academic_year are skipped.

The worker accepts ctx["db_sessionmaker"].  We pass the conftest-patched
app.core.database.async_session_factory so the worker operates on the same
in-memory SQLite DB as db_session.

Note: year_rollover uses select(User) which is filtered by the global soft-delete
event listener (database.py) to only return users where deleted_at IS NULL.
Advancing years for deleted users is intentionally prevented.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.database as c_db
from app.models.user import User, UserRole
from app.workers.year_rollover import ROLLOVER_MAP, year_rollover


async def _make_user(db: AsyncSession, academic_year: str | None) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@test.example",
        display_name="Test",
        role=UserRole.STUDENT,
        academic_year=academic_year,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def test_year_rollover_1a_to_2a(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, "1A")

    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2030)

    await db_session.refresh(user)
    assert user.academic_year == "2A"


async def test_year_rollover_2a_to_3a_plus(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, "2A")

    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2030)

    await db_session.refresh(user)
    assert user.academic_year == "3A+"


async def test_year_rollover_3a_plus_stays_capped(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, "3A+")

    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2030)

    await db_session.refresh(user)
    assert user.academic_year == "3A+"


async def test_year_rollover_skips_users_without_academic_year(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, None)

    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2030)

    await db_session.refresh(user)
    assert user.academic_year is None


async def test_year_rollover_noop_on_empty_db(db_session: AsyncSession) -> None:
    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2030)


async def test_year_rollover_duplicate_run_is_idempotent(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, "1A")

    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2030)
    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2030)

    await db_session.refresh(user)
    assert user.academic_year == "2A"


async def test_year_rollover_next_year_advances_again(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, "1A")

    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2030)
    await year_rollover({"db_sessionmaker": c_db.async_session_factory}, target_year=2031)

    await db_session.refresh(user)
    assert user.academic_year == "3A+"


async def test_year_rollover_rollover_map_completeness() -> None:
    """ROLLOVER_MAP must define a transition for every known year value."""
    known_years = {"1A", "2A", "3A+"}
    for year in known_years:
        assert year in ROLLOVER_MAP, f"No rollover mapping for academic_year={year!r}"
