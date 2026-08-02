from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete

from app.core.database.post_commit import dispatch_pending_outbox
from app.models.outbox import OutboxJob


async def dispatch_outbox(ctx: dict[str, Any]) -> None:
    """Retry durable post-commit jobs that could not reach ARQ earlier."""
    session_factory = ctx["db_sessionmaker"]
    async with session_factory() as session:
        await dispatch_pending_outbox(session, limit=500)
        await session.execute(
            delete(OutboxJob).where(
                (OutboxJob.delivered_at < datetime.now(UTC) - timedelta(days=7))
                | (OutboxJob.abandoned_at < datetime.now(UTC) - timedelta(days=30))
            )
        )
        await session.commit()
