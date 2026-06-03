"""
Data-behavior tests for the view_reset worker.

reset_daily_views:
  - Accumulates views_today into views_14d, then zeros views_today.
  - Only touches rows where views_today > 0.

reset_14d_views:
  - Zeros views_14d for all materials.
  - Only touches rows where views_14d > 0.

The worker accepts ctx["db_sessionmaker"].  We pass the conftest-patched
app.core.database.async_session_factory so the worker operates on the same
in-memory SQLite DB as db_session.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database as c_db
from app.models.material import Material
from app.workers.view_reset import reset_14d_views, reset_daily_views


async def _make_material(db: AsyncSession, views_today: int = 0, views_14d: int = 0) -> Material:
    m = Material(
        id=uuid.uuid4(),
        title="Test",
        slug=f"test-{uuid.uuid4().hex[:8]}",
        type="document",
        current_version=1,
        views_today=views_today,
        views_14d=views_14d,
    )
    db.add(m)
    await db.flush()
    return m


async def test_reset_daily_views_accumulates_into_14d(db_session: AsyncSession) -> None:
    m = await _make_material(db_session, views_today=5, views_14d=10)

    await reset_daily_views({"db_sessionmaker": c_db.async_session_factory})

    await db_session.refresh(m)
    assert m.views_14d == 15
    assert m.views_today == 0


async def test_reset_daily_views_skips_zero_views_today(db_session: AsyncSession) -> None:
    m = await _make_material(db_session, views_today=0, views_14d=7)

    await reset_daily_views({"db_sessionmaker": c_db.async_session_factory})

    await db_session.refresh(m)
    assert m.views_14d == 7
    assert m.views_today == 0


async def test_reset_daily_views_updates_last_view_reset(db_session: AsyncSession) -> None:
    m = await _make_material(db_session, views_today=3, views_14d=0)
    original_reset = m.last_view_reset

    await reset_daily_views({"db_sessionmaker": c_db.async_session_factory})

    await db_session.refresh(m)
    assert m.last_view_reset is not None
    assert m.last_view_reset >= original_reset if original_reset else True


async def test_reset_14d_views_zeros_counter(db_session: AsyncSession) -> None:
    m = await _make_material(db_session, views_today=0, views_14d=42)

    await reset_14d_views({"db_sessionmaker": c_db.async_session_factory})

    await db_session.refresh(m)
    assert m.views_14d == 0


async def test_reset_14d_views_skips_zero_14d(db_session: AsyncSession) -> None:
    m = await _make_material(db_session, views_today=8, views_14d=0)

    await reset_14d_views({"db_sessionmaker": c_db.async_session_factory})

    await db_session.refresh(m)
    assert m.views_today == 8


async def test_reset_14d_views_does_not_touch_views_today(db_session: AsyncSession) -> None:
    m = await _make_material(db_session, views_today=3, views_14d=10)

    await reset_14d_views({"db_sessionmaker": c_db.async_session_factory})

    await db_session.refresh(m)
    assert m.views_today == 3
    assert m.views_14d == 0
