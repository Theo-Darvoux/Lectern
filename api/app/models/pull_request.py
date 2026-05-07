from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.security import VirusScanResult

if TYPE_CHECKING:
    from app.models.user import User


class PRStatus(enum.StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PullRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pull_requests"

    type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="batch",
        server_default="batch",
    )
    status: Mapped[PRStatus] = mapped_column(
        Enum(PRStatus, values_callable=lambda e: [m.value for m in e]),
        default=PRStatus.OPEN,
        server_default="open",
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    # Populated on approval: enriched copy of operations with result_id and
    # result_browse_path. The original payload is never mutated.
    applied_result: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    summary_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    virus_scan_result: Mapped[VirusScanResult] = mapped_column(
        String(20),
        default=VirusScanResult.PENDING,
        server_default="pending",
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reverts_pr_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="SET NULL"), nullable=True
    )
    reverted_by_pr_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="SET NULL"), nullable=True
    )

    author: Mapped[User | None] = relationship(
        back_populates="pull_requests", foreign_keys=[author_id]
    )
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])
    reverts_pr: Mapped[PullRequest | None] = relationship(
        foreign_keys=[reverts_pr_id], remote_side="PullRequest.id"
    )
    reverted_by_pr: Mapped[PullRequest | None] = relationship(
        foreign_keys=[reverted_by_pr_id], remote_side="PullRequest.id"
    )
    comments: Mapped[list[PRComment]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )
    file_claims: Mapped[list[PRFileClaim]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )

    @property
    def expires_at(self) -> datetime | None:
        if self.status != PRStatus.OPEN:
            return None
        from datetime import timedelta

        return self.updated_at + timedelta(days=settings.pr_expiry_days)

    @property
    def revert_grace_expires_at(self) -> datetime | None:
        if self.status != PRStatus.APPROVED or self.approved_at is None:
            return None
        from datetime import timedelta

        approved_at = self.approved_at
        if approved_at.tzinfo is None:
            approved_at = approved_at.replace(tzinfo=UTC)

        return approved_at + timedelta(days=settings.pr_revert_grace_days)

    @property
    def is_revertable(self) -> bool:

        expires = self.revert_grace_expires_at
        return (
            self.status == PRStatus.APPROVED
            and self.type != "revert"
            and self.reverted_by_pr_id is None
            and expires is not None
            and datetime.now(UTC) < expires
            and self.applied_result is not None
        )

    def can_be_managed_by(self, user: User) -> bool:
        """Standard RBAC helper: author or moderator."""
        return user.is_moderator or self.author_id == user.id


class PRFileClaim(Base):
    """
    Tracks which file_keys are claimed by open pull requests.

    The PRIMARY KEY on file_key enforces that only one open PR can reference
    a given file at a time, replacing the previous Redis global lock + JSONB scan.
    Claims are deleted when the PR is approved or rejected.
    """

    __tablename__ = "pr_file_claims"

    file_key: Mapped[str] = mapped_column(Text, primary_key=True)
    pr_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )

    pull_request: Mapped[PullRequest] = relationship(back_populates="file_claims")


class PRComment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pr_comments"

    pr_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_comments.id", ondelete="CASCADE")
    )

    pull_request: Mapped[PullRequest] = relationship(back_populates="comments")
    author: Mapped[User | None] = relationship()
    parent: Mapped[PRComment | None] = relationship(remote_side="PRComment.id")
