from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InstallationState(Base):
    """Durable one-way marker that HTTP bootstrap has been consumed."""

    __tablename__ = "installation_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_installation_state_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    bootstrapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
