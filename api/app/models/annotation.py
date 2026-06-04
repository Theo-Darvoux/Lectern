from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.material import Material
    from app.models.user import User


class Annotation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "annotations"
    __allow_unmapped__ = True

    _replies: list[Annotation]

    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("material_versions.id", ondelete="SET NULL")
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text, nullable=False)

    page: Mapped[int | None] = mapped_column(Integer)
    selection_text: Mapped[str | None] = mapped_column(Text)
    position_data: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("annotations.id", ondelete="CASCADE")
    )
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("annotations.id", ondelete="SET NULL")
    )

    material: Mapped[Material] = relationship(back_populates="annotations")
    author: Mapped[User | None] = relationship(back_populates="annotations")
    thread_root: Mapped[Annotation | None] = relationship(
        remote_side="Annotation.id", foreign_keys=[thread_id], post_update=True
    )
    reply_to: Mapped[Annotation | None] = relationship(
        remote_side="Annotation.id", foreign_keys=[reply_to_id], post_update=True
    )
