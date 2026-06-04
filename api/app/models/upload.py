from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class Upload(UUIDMixin, Base):
    """Persistent lifecycle record for every upload attempt.

    Created by the upload router on initiation; updated by the background worker
    at each pipeline stage. Survives API/worker restarts and provides an audit
    trail independent of the Redis TTL.
    """

    __tablename__ = "uploads"
    __table_args__ = (
        Index("ix_uploads_user_status", "user_id", "status"),
        Index("ix_uploads_sha256", "sha256"),
        Index("ix_uploads_content_sha256", "content_sha256"),
        Index("ix_uploads_upload_id", "upload_id", unique=True),
    )

    upload_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    quarantine_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_stage: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cas_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    cas_ref_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
