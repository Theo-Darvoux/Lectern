from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.material import Material
    from app.models.tag import Tag


class DirectoryType(enum.StrEnum):
    MODULE = "module"
    FOLDER = "folder"


class Directory(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "directories"
    __table_args__ = (
        Index(
            "uq_directory_parent_slug",
            "parent_id",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("directories.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[DirectoryType] = mapped_column(
        Enum(DirectoryType, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    like_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    parent: Mapped[Directory | None] = relationship(
        back_populates="children", remote_side="Directory.id"
    )
    children: Mapped[list[Directory]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", passive_deletes=True
    )
    materials: Mapped[list[Material]] = relationship(
        back_populates="directory", cascade="all, delete-orphan", passive_deletes=True
    )
    tags: Mapped[list[Tag]] = relationship(secondary="directory_tags", back_populates="directories")
    likes: Mapped[list[DirectoryLike]] = relationship(
        back_populates="directory", cascade="all, delete-orphan"
    )
    favourites: Mapped[list[DirectoryFavourite]] = relationship(
        back_populates="directory", cascade="all, delete-orphan"
    )


class DirectoryLike(UUIDMixin, Base):
    __tablename__ = "directory_likes"
    __table_args__ = (
        UniqueConstraint("user_id", "directory_id", name="uq_directory_like_user_directory"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    directory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("directories.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    directory: Mapped[Directory] = relationship(back_populates="likes")


class DirectoryFavourite(UUIDMixin, Base):
    __tablename__ = "directory_favourites"
    __table_args__ = (
        UniqueConstraint("user_id", "directory_id", name="uq_directory_favourite_user_directory"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    directory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("directories.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    directory: Mapped[Directory] = relationship(back_populates="favourites")
