import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

ROLLOVER_MAP = {"1A": "2A", "2A": "3A+", "3A+": "3A+"}


async def year_rollover(
    ctx: dict,  # type: ignore[type-arg]
    *,
    target_year: int | None = None,
) -> None:
    """Advance academic years at most once for a given calendar year.

    The run marker and user changes commit atomically. The unique marker is
    flushed before any user is changed, so concurrent or operator-triggered
    duplicate invocations cannot apply the non-idempotent mapping twice.
    """
    from app.models.scheduled_job_run import ScheduledJobRun
    from app.models.user import User

    run_key = str(target_year if target_year is not None else datetime.now(UTC).year)

    session_factory = ctx.get("db_sessionmaker")
    owned_engine: AsyncEngine | None = None
    if session_factory is None:
        owned_engine = create_async_engine(settings.database_url)
        session_factory = async_sessionmaker(owned_engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
            try:
                async with db.begin_nested():
                    db.add(ScheduledJobRun(job_name="year_rollover", run_key=run_key))
                    await db.flush()
            except IntegrityError:
                logger.info("Year rollover %s was already completed; skipping", run_key)
                return

            result = await db.execute(
                select(User).where(
                    User.academic_year.isnot(None),
                )
            )
            users = result.scalars().all()
            count = 0
            for user in users:
                if user.academic_year:
                    new_year = ROLLOVER_MAP.get(user.academic_year)
                    if new_year and new_year != user.academic_year:
                        user.academic_year = new_year
                        count += 1

            await db.commit()
            logger.info("Year rollover %s: updated %d users", run_key, count)
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()
