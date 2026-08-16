from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.directory import Directory
    from app.models.material import Material


class Collection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("user_id", "name_key", name="uq_collection_user_name_key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Case-insensitive, whitespace-normalized key maintained by the application.
    # Keeping it explicit makes the uniqueness rule portable across PostgreSQL and
    # the SQLite test suite.
    name_key: Mapped[str] = mapped_column(String(160), nullable=False)

    items: Mapped[list[CollectionItem]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CollectionItem.created_at.desc()",
    )


class CollectionItem(UUIDMixin, Base):
    __tablename__ = "collection_items"
    __table_args__ = (
        CheckConstraint(
            "(material_id IS NOT NULL AND directory_id IS NULL) OR "
            "(material_id IS NULL AND directory_id IS NOT NULL)",
            name="ck_collection_item_exactly_one_target",
        ),
        UniqueConstraint(
            "collection_id",
            "material_id",
            name="uq_collection_item_collection_material",
        ),
        UniqueConstraint(
            "collection_id",
            "directory_id",
            name="uq_collection_item_collection_directory",
        ),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    material_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=True
    )
    directory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("directories.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    collection: Mapped[Collection] = relationship(back_populates="items")
    material: Mapped[Material | None] = relationship()
    directory: Mapped[Directory | None] = relationship()

    @property
    def target_type(self) -> str:
        return "material" if self.material_id is not None else "directory"

    @property
    def target_id(self) -> uuid.UUID:
        target_id = self.material_id or self.directory_id
        if target_id is None:  # guarded by the database check constraint
            raise RuntimeError("Collection item has no target")
        return target_id
