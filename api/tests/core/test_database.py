from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Select
from sqlalchemy.orm import ORMExecuteState

from app.core.database.database import _soft_delete_filter, get_db
from app.core.database.post_commit import register_transaction_callbacks


@pytest.mark.asyncio
async def test_get_db_yields_session_and_commits():
    mock_session = AsyncMock()
    mock_session.info = {}

    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = mock_session
    session_cm.__aexit__.return_value = None

    with (
        patch("app.core.database.database.async_session_factory", return_value=session_cm),
        patch(
            "app.core.database.post_commit.dispatch_post_commit_actions", new_callable=AsyncMock
        ) as mock_dispatch,
    ):
        async for session in get_db():
            assert session == mock_session

        mock_session.commit.assert_called_once()
        mock_dispatch.assert_called_once_with(mock_session)


@pytest.mark.asyncio
async def test_get_db_rolls_back_on_error():
    mock_session = AsyncMock()
    mock_session.info = {}

    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = mock_session
    session_cm.__aexit__.return_value = None

    with (
        patch("app.core.database.database.async_session_factory", return_value=session_cm),
        patch("app.core.database.post_commit.dispatch_post_commit_actions", new_callable=AsyncMock),
    ):
        gen = get_db()
        session = await gen.__anext__()
        assert session == mock_session

        with pytest.raises(ValueError, match="Test failure"):
            await gen.athrow(ValueError("Test failure"))

        mock_session.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_get_db_preserves_external_resources_when_commit_outcome_is_unknown():
    mock_session = AsyncMock()
    mock_session.info = {}
    mock_session.commit.side_effect = RuntimeError("database commit failed")
    rollback_resource = AsyncMock()
    finalize_resource = AsyncMock()

    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = mock_session
    session_cm.__aexit__.return_value = None

    with (
        patch("app.core.database.database.async_session_factory", return_value=session_cm),
        patch("app.core.database.post_commit.dispatch_post_commit_actions", new_callable=AsyncMock),
    ):
        gen = get_db()
        session = await gen.__anext__()
        assert register_transaction_callbacks(
            session,
            on_rollback=rollback_resource,
            on_commit=finalize_resource,
        )

        with pytest.raises(RuntimeError, match="database commit failed"):
            await gen.__anext__()

    mock_session.rollback.assert_awaited_once()
    rollback_resource.assert_not_awaited()
    finalize_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_db_compensates_external_resources_before_commit_is_attempted():
    mock_session = AsyncMock()
    mock_session.info = {}
    rollback_resource = AsyncMock()
    finalize_resource = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = mock_session
    session_cm.__aexit__.return_value = None

    with patch("app.core.database.database.async_session_factory", return_value=session_cm):
        dependency = get_db()
        session = await dependency.__anext__()
        assert register_transaction_callbacks(
            session,
            on_rollback=rollback_resource,
            on_commit=finalize_resource,
        )
        with pytest.raises(ValueError, match="route failed"):
            await dependency.athrow(ValueError("route failed"))

    rollback_resource.assert_awaited_once()
    finalize_resource.assert_not_awaited()


def test_soft_delete_filter_skips_non_select():
    mock_state = MagicMock(spec=ORMExecuteState)
    mock_state.is_select = False
    _soft_delete_filter(mock_state)
    assert not mock_state.statement.options.called


def test_soft_delete_filter_skips_when_include_deleted_is_true():
    mock_state = MagicMock(spec=ORMExecuteState)
    mock_state.is_select = True
    mock_state.execution_options = {"include_deleted": True}

    _soft_delete_filter(mock_state)
    assert not mock_state.statement.options.called


def test_soft_delete_filter_applies_criteria_on_select():
    mock_state = MagicMock(spec=ORMExecuteState)
    mock_state.is_select = True
    mock_state.execution_options = {}
    mock_stmt = MagicMock(spec=Select)
    mock_state.statement = mock_stmt

    _soft_delete_filter(mock_state)
    assert mock_stmt.options.called


@pytest.mark.asyncio
async def test_db_rollback_failure_still_attempts_external_compensation():
    """When session.rollback() itself fails, external compensation must still run.

    This exercises the control-flow ordering in get_db(): settle_awaitable is
    called on session.rollback(), and even when it errors, the code proceeds to
    rollback_transaction_callbacks(). Before the settle_awaitable fix, an
    ordinary exception from rollback() could escape before compensation ran.
    """
    compensated = AsyncMock()

    async def noop() -> None:
        return None

    mock_session = AsyncMock()
    mock_session.info = {}
    mock_session.rollback = AsyncMock(side_effect=OSError("postgres rollback failed"))

    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = mock_session
    session_cm.__aexit__.return_value = None

    with patch("app.core.database.database.async_session_factory", return_value=session_cm):
        gen = get_db()
        session = await gen.__anext__()

        assert register_transaction_callbacks(
            session,
            on_rollback=compensated,
            on_commit=noop,
        )

        with pytest.raises(RuntimeError, match="Database rollback failed"):
            await gen.athrow(ValueError("request failed"))

    compensated.assert_awaited_once()
