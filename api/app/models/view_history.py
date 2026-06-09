import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class ViewHistory(UUIDMixin, Base):
    __tablename__ = "view_history"
    __table_args__ = (
        UniqueConstraint("user_id", "material_id", name="uq_view_history_user_material"),
        Index("idx_view_history_user", "user_id", "viewed_at"),
        Index("ix_view_history_material_id", "material_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
