import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.config import settings

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
        persist_post_commit_jobs,
    )

    async with async_session_factory() as session:
        jobs: list[tuple[Any, ...]] = []
        session.info[PostCommitKey.JOBS] = jobs
        try:
            yield session
            await persist_post_commit_jobs(session)
            await session.commit()
            await dispatch_post_commit_actions(session)
        except Exception:
            await session.rollback()
            raise
        finally:
            session.info.pop(PostCommitKey.JOBS, None)
            session.info.pop(PostCommitKey.SSE, None)
