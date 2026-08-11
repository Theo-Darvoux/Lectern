from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, or_

from app.core.database.post_commit import dispatch_pending_outbox
from app.models.outbox import OutboxJob


async def dispatch_outbox(ctx: dict[str, Any]) -> None:
    """Retry durable post-commit jobs that could not reach ARQ earlier."""
    session_factory = ctx["db_sessionmaker"]
    async with session_factory() as session:
        await dispatch_pending_outbox(session, limit=500)
        now = datetime.now(UTC)
        await session.execute(
            delete(OutboxJob).where(
                or_(
                    and_(
                        OutboxJob.job_name != "delete_indexed_item",
                        OutboxJob.delivered_at < now - timedelta(days=7),
                    ),
                    and_(
                        OutboxJob.job_name == "delete_indexed_item",
                        OutboxJob.completed_at < now - timedelta(days=7),
                    ),
                    and_(
                        OutboxJob.job_name != "delete_indexed_item",
                        OutboxJob.abandoned_at < now - timedelta(days=30),
                    ),
                )
            )
        )
        await session.commit()
