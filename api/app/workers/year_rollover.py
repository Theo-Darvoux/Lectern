import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

ROLLOVER_MAP = {"1A": "2A", "2A": "3A+", "3A+": "3A+"}


async def year_rollover(ctx: dict) -> None:  # type: ignore[type-arg]
    from app.models.user import User

    session_factory = ctx.get("db_sessionmaker")
    owned_engine: AsyncEngine | None = None
    if session_factory is None:
        owned_engine = create_async_engine(settings.database_url)
        session_factory = async_sessionmaker(owned_engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
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
            logger.info("Year rollover: updated %d users", count)
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()
