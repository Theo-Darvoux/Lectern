from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.annotation import Annotation
    from app.models.comment import Comment
    from app.models.notification import Notification
    from app.models.pull_request import PullRequest


class UserRole(enum.StrEnum):
    PENDING = "pending"
    STUDENT = "student"
    MODERATOR = "moderator"
    BUREAU = "bureau"
    VIEUX = "vieux"
    GUEST = "guest"


class User(UUIDMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, values_callable=lambda e: [m.value for m in e]),
        default=UserRole.STUDENT,
        server_default="student",
    )
    bio: Mapped[str | None] = mapped_column(Text)
    academic_year: Mapped[str | None] = mapped_column(String(10))
    gdpr_consent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    gdpr_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_guest(self) -> bool:
        return self.role == UserRole.GUEST

    @property
    def is_moderator(self) -> bool:
        return self.role in (UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX)

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.BUREAU, UserRole.VIEUX)

    @property
    def is_staff(self) -> bool:
        return self.is_moderator

    pull_requests: Mapped[list[PullRequest]] = relationship(
        back_populates="author",
        foreign_keys="PullRequest.author_id",
    )
    annotations: Mapped[list[Annotation]] = relationship(back_populates="author")
    comments: Mapped[list[Comment]] = relationship(back_populates="author")
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
