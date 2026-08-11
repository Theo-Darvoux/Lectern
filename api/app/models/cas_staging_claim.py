from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class CasStagingClaim(UUIDMixin, Base):
    """Single-use proof that a user staged a generated CAS object."""

    __tablename__ = "cas_staging_claims"
    __table_args__ = (
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_cas_staging_claims_size_bytes_nonnegative",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL is reserved for pre-migration claims whose authoritative S3 size was
    # not available to Alembic. New staging admissions always persist this value.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
