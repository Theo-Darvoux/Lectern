from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class AllowedDomain(UUIDMixin, Base):
    """Per-domain auth policy. Domain stored without leading @."""

    __tablename__ = "allowed_domains"

    __table_args__ = (UniqueConstraint("domain", name="uq_allowed_domains_domain"),)

    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
