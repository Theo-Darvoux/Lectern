from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Select
from sqlalchemy.orm import ORMExecuteState

from app.core.database.database import _soft_delete_filter, get_db


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
