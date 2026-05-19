"""add bg_watermark fields to auth_configs

Revision ID: 009
Revises: 008
Create Date: 2026-05-19
"""

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("bg_watermark_url", sa.String(255), nullable=True))
    op.add_column("auth_configs", sa.Column("bg_watermark_opacity", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "bg_watermark_opacity")
    op.drop_column("auth_configs", "bg_watermark_url")
