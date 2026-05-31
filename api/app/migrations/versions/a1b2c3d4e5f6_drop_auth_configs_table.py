"""drop auth_configs table

Revision ID: a1b2c3d4e5f6
Revises: fe2b420e9ac1
Create Date: 2026-05-31 00:00:00.000000

Run this migration ONLY after:
1. All live DB values have been exported via `config-export-env` and verified in .env.
2. A diff of /api/auth/methods between old and new instances is clean (empty).

The down-revision recreates an empty auth_configs table so a code rollback is safe
(values continue to come from env regardless).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "fe2b420e9ac1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("auth_configs")


def downgrade() -> None:
    op.create_table(
        "auth_configs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("totp_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("google_oauth_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("google_client_id", sa.String(255), nullable=True),
        sa.Column("classic_auth_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("allow_all_domains", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("guest_access_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "auto_approve_all_domains", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("jwt_access_expire_days", sa.Integer(), server_default="7", nullable=False),
        sa.Column("jwt_refresh_expire_days", sa.Integer(), server_default="31", nullable=False),
        sa.Column("smtp_host", sa.String(255), nullable=True),
        sa.Column("smtp_ip", sa.String(255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_user", sa.String(255), nullable=True),
        sa.Column("smtp_password", sa.String(255), nullable=True),
        sa.Column("smtp_from", sa.String(255), nullable=True),
        sa.Column("smtp_sender_name", sa.String(100), nullable=True),
        sa.Column("smtp_avatar_url", sa.String(255), nullable=True),
        sa.Column("smtp_use_tls", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("s3_endpoint", sa.String(255), nullable=True),
        sa.Column("s3_access_key", sa.String(255), nullable=True),
        sa.Column("s3_secret_key", sa.String(255), nullable=True),
        sa.Column("s3_bucket", sa.String(100), nullable=True),
        sa.Column("s3_public_endpoint", sa.String(255), nullable=True),
        sa.Column("s3_region", sa.String(50), nullable=True),
        sa.Column("s3_use_ssl", sa.Boolean(), nullable=False),
        sa.Column("max_storage_gb", sa.Integer(), nullable=True),
        sa.Column("max_file_size_mb", sa.Integer(), nullable=True),
        sa.Column("max_image_size_mb", sa.Integer(), nullable=True),
        sa.Column("max_audio_size_mb", sa.Integer(), nullable=True),
        sa.Column("max_video_size_mb", sa.Integer(), nullable=True),
        sa.Column("max_document_size_mb", sa.Integer(), nullable=True),
        sa.Column("max_office_size_mb", sa.Integer(), nullable=True),
        sa.Column("max_text_size_mb", sa.Integer(), nullable=True),
        sa.Column("pdf_quality", sa.Integer(), nullable=True),
        sa.Column("video_compression_profile", sa.String(20), nullable=True),
        sa.Column("thumbnail_quality", sa.Integer(), nullable=True),
        sa.Column("thumbnail_size_px", sa.Integer(), nullable=True),
        sa.Column("allowed_extensions", sa.Text(), nullable=True),
        sa.Column("allowed_mime_types", sa.Text(), nullable=True),
        sa.Column("site_name", sa.String(100), nullable=True),
        sa.Column("site_name_style", sa.Text(), nullable=True),
        sa.Column("site_description", sa.Text(), nullable=True),
        sa.Column("site_logo_url", sa.String(255), nullable=True),
        sa.Column("site_favicon_url", sa.String(255), nullable=True),
        sa.Column("primary_color", sa.String(10), nullable=True),
        sa.Column("footer_text", sa.Text(), nullable=True),
        sa.Column("organization_url", sa.String(255), nullable=True),
        sa.Column("og_image_url", sa.String(255), nullable=True),
        sa.Column("bg_watermark_url", sa.String(255), nullable=True),
        sa.Column("bg_watermark_opacity_light", sa.Float(), nullable=True),
        sa.Column("bg_watermark_opacity_dark", sa.Float(), nullable=True),
        sa.Column("footer_logo_url", sa.String(255), nullable=True),
        sa.Column("legal_name", sa.String(255), nullable=True),
        sa.Column("legal_address", sa.Text(), nullable=True),
        sa.Column("legal_siret", sa.String(255), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("dpo_email", sa.String(255), nullable=True),
        sa.Column("dpo_address", sa.Text(), nullable=True),
        sa.Column("data_transfers", sa.Text(), nullable=True),
        sa.Column("legal_version", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
