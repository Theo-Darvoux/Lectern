import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.config import settings
from app.core.security.async_utils import settle_awaitable

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_async_engine(
    settings.database_url,
    echo=False,
    query_cache_size=1200,
    **(
        {}
        if _is_sqlite
        else {
            "pool_size": 20,
            "max_overflow": 10,
            "pool_timeout": 30,
            "pool_pre_ping": True,
            "pool_recycle": 1800,
        }
    ),
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

logger = logging.getLogger(__name__)


@event.listens_for(Session, "do_orm_execute")
def _soft_delete_filter(execute_state: ORMExecuteState) -> None:
    if not execute_state.is_select:
        return
    if execute_state.execution_options.get("include_deleted", False):
        return

    from app.models.directory import Directory
    from app.models.material import Material, MaterialVersion
    from app.models.user import User

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(Material, Material.deleted_at.is_(None), include_aliases=True),
        with_loader_criteria(Directory, Directory.deleted_at.is_(None), include_aliases=True),
        with_loader_criteria(
            MaterialVersion, MaterialVersion.deleted_at.is_(None), include_aliases=True
        ),
        with_loader_criteria(User, User.deleted_at.is_(None), include_aliases=True),
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    from app.core.database.post_commit import (
        PostCommitKey,
        dispatch_post_commit_actions,
        finalize_transaction_callbacks,
        persist_post_commit_jobs,
        rollback_transaction_callbacks,
    )

    async with async_session_factory() as session:
        jobs: list[tuple[Any, ...]] = []
        session.info[PostCommitKey.JOBS] = jobs
        session.info[PostCommitKey.MANAGED_TRANSACTION] = True
        commit_attempted = False
        try:
            yield session
            await persist_post_commit_jobs(session)
            commit_attempted = True
            await session.commit()
        except BaseException:
            _result, rollback_error, rollback_cancellation = await settle_awaitable(
                session.rollback()
            )
            compensation_error: BaseException | None = None
            if commit_attempted:
                # COMMIT acknowledgement is ambiguous after connection loss or
                # cancellation. Destructive compensation could delete bytes
                # referenced by a transaction PostgreSQL actually committed.
                # Preserve external data and recovery journals for reconciliation.
                session.info.pop(PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS, None)
                session.info.pop(PostCommitKey.TRANSACTION_COMMIT_CALLBACKS, None)
                logger.error(
                    "Database COMMIT failed with unknown outcome; preserving external mutations"
                )
            else:
                try:
                    await rollback_transaction_callbacks(session)
                except BaseException as exc:
                    compensation_error = exc
            if compensation_error is not None and not isinstance(
                compensation_error, asyncio.CancelledError
            ):
                raise RuntimeError(
                    "Database transaction failed and external-resource compensation was incomplete"
                ) from compensation_error
            if rollback_error is not None:
                raise RuntimeError("Database rollback failed") from rollback_error
            if rollback_cancellation is not None:
                raise rollback_cancellation
            if compensation_error is not None:
                raise compensation_error
            raise
        else:
            await finalize_transaction_callbacks(session)
            await dispatch_post_commit_actions(session)
        finally:
            session.info.pop(PostCommitKey.JOBS, None)
            session.info.pop(PostCommitKey.SSE, None)
            session.info.pop(PostCommitKey.USER_SSE, None)
            session.info.pop(PostCommitKey.MANAGED_TRANSACTION, None)
            session.info.pop(PostCommitKey.TRANSACTION_COMMIT_CALLBACKS, None)
            session.info.pop(PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS, None)
