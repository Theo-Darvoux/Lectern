import logging
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, with_loader_criteria

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_async_engine(
    settings.database_url,
    echo=False,  # DO NOT USE settings.is_dev as it causes massive I/O load under load
    query_cache_size=1200,
    **(
        {}
        if _is_sqlite
        else {
            "pool_size": 150,
            "max_overflow": 350,
            "pool_timeout": 5,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
        }
    ),
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

logger = logging.getLogger(__name__)


@event.listens_for(Session, "do_orm_execute")
def _soft_delete_filter(execute_state):  # type: ignore[no-untyped-def]
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


def _coalesce_index_jobs(jobs: list) -> list:  # type: ignore[type-arg]
    """Coalesce consecutive index_material / index_directory jobs into batch calls.

    Preserves relative order of non-index jobs (delete_indexed_item,
    delete_storage_objects, etc.) so that deletes always execute before or after
    the adjacent index operations as originally ordered.
    """
    result = []
    i = 0
    while i < len(jobs):
        kind = jobs[i][0]
        if kind == "index_material":
            batch: list = [jobs[i][1]]  # type: ignore[type-arg]
            i += 1
            while i < len(jobs) and jobs[i][0] == "index_material":
                batch.append(jobs[i][1])
                i += 1
            if len(batch) == 1:
                result.append(("index_material", batch[0]))
            else:
                result.append(("index_materials_batch", batch))
        elif kind == "index_directory":
            batch = [jobs[i][1]]
            i += 1
            while i < len(jobs) and jobs[i][0] == "index_directory":
                batch.append(jobs[i][1])
                i += 1
            if len(batch) == 1:
                result.append(("index_directory", batch[0]))
            else:
                result.append(("index_directories_batch", batch))
        else:
            result.append(jobs[i])
            i += 1
    return result


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    import typing

    async with async_session_factory() as session:
        jobs: list[tuple[typing.Any, ...]] = []
        session.info["post_commit_jobs"] = jobs
        try:
            yield session
            await session.commit()

            sse_broadcasts = session.info.pop("post_commit_sse_broadcasts", [])
            if sse_broadcasts:
                from app.core.sse import broadcast_to_topic

                for topic, event in sse_broadcasts:
                    broadcast_to_topic(topic, event)

            if jobs:
                import app.core.redis as redis_core

                coalesced = _coalesce_index_jobs(jobs)

                if redis_core.arq_pool:
                    for job in coalesced:
                        try:
                            await redis_core.arq_pool.enqueue_job(*job)
                        except Exception as e:
                            logger.critical(
                                "CRITICAL: Failed to enqueue background job after commit: %s. Job data: %s",
                                e,
                                job,
                            )
                else:
                    logger.critical(
                        "CRITICAL: No arq_pool available to enqueue jobs after commit. Jobs: %s",
                        coalesced,
                    )
        except Exception:
            await session.rollback()
            raise
