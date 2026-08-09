from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ScheduledJobRun(Base):
    """Durable identity for a non-repeatable scheduled job occurrence."""

    __tablename__ = "scheduled_job_runs"

    job_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
