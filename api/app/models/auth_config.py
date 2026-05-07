from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class AuthConfig(UUIDMixin, Base):
    """Single-row global auth configuration. Created by migration seed."""

    __tablename__ = "auth_configs"

    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    google_oauth_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    google_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    classic_auth_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    allow_all_domains: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    auto_approve_all_domains: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    jwt_access_expire_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    jwt_refresh_expire_days: Mapped[int] = mapped_column(Integer, default=31, server_default="31")

    # SMTP Settings
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_ip: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # S3 Storage Settings
    s3_endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    s3_access_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    s3_secret_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    s3_bucket: Mapped[str | None] = mapped_column(String(100), nullable=True)
    s3_public_endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    s3_region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    s3_use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    max_storage_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- File Pipeline & Limits ---
    max_file_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_image_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_audio_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_video_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_document_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_office_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_text_size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    pdf_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_compression_profile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    thumbnail_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_size_px: Mapped[int | None] = mapped_column(Integer, nullable=True)

    allowed_extensions: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_mime_types: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Branding
    site_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    site_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_logo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site_favicon_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String(10), nullable=True)
    footer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    og_image_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legal_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_siret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dpo_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dpo_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_transfers: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AllowedDomain(UUIDMixin, Base):
    """Per-domain auth policy. Domain stored without leading @."""

    __tablename__ = "allowed_domains"

    __table_args__ = (UniqueConstraint("domain", name="uq_allowed_domains_domain"),)

    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
