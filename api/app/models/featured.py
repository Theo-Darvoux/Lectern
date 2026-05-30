from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.directory import Directory
    from app.models.material import Material
    from app.models.user import User


class FeaturedItem(UUIDMixin, Base):
    __tablename__ = "featured_items"

    material_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=True
    )
    directory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("directories.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    material: Mapped[Material | None] = relationship(lazy="joined")
    directory: Mapped[Directory | None] = relationship(lazy="joined")
    creator: Mapped[User | None] = relationship(foreign_keys=[created_by])
