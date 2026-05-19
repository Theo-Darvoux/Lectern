"""split bg_watermark_opacity into light/dark variants

Revision ID: 010
Revises: 009
Create Date: 2026-05-19
"""

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_configs", sa.Column("bg_watermark_opacity_light", sa.Float(), nullable=True)
    )
    op.add_column("auth_configs", sa.Column("bg_watermark_opacity_dark", sa.Float(), nullable=True))
    # Copy existing single opacity value into both new columns, then drop the old one
    op.execute(
        "UPDATE auth_configs SET bg_watermark_opacity_light = bg_watermark_opacity, "
        "bg_watermark_opacity_dark = bg_watermark_opacity"
    )
    op.drop_column("auth_configs", "bg_watermark_opacity")


def downgrade() -> None:
    op.add_column("auth_configs", sa.Column("bg_watermark_opacity", sa.Float(), nullable=True))
    op.execute("UPDATE auth_configs SET bg_watermark_opacity = bg_watermark_opacity_light")
    op.drop_column("auth_configs", "bg_watermark_opacity_dark")
    op.drop_column("auth_configs", "bg_watermark_opacity_light")
