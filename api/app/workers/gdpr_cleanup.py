import logging
import typing
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.user import User
from app.services.user import hard_delete_user

logger = logging.getLogger(__name__)

GDPR_RETENTION_DAYS = 30


async def gdpr_cleanup(ctx: dict[str, typing.Any]) -> None:
    logger.info("Running GDPR cleanup cron job")
    try:
        from app.core.database.database import async_session_factory
        from app.core.database.post_commit import (
            PostCommitKey,
            dispatch_post_commit_actions,
            persist_post_commit_jobs,
        )

        cutoff = datetime.now(UTC) - timedelta(days=GDPR_RETENTION_DAYS)

        async with async_session_factory() as db:
            db.info[PostCommitKey.JOBS] = []
            result = await db.execute(
                select(User)
                .where(
                    User.deleted_at.is_not(None),
                    User.deleted_at <= cutoff,
                )
                .execution_options(include_deleted=True)
            )
            users_to_delete = result.scalars().all()

            if not users_to_delete:
                logger.info("No users to clean up")
                return

            for user in users_to_delete:
                logger.info("Hard-deleting user %s (%s)", user.id, user.email)
                await hard_delete_user(db, user)

            await persist_post_commit_jobs(db)
            await db.commit()
            await dispatch_post_commit_actions(db)
            logger.info("GDPR cleanup complete: %d users purged", len(users_to_delete))

    except Exception as e:
        logger.error("GDPR cleanup failed: %s", e)
