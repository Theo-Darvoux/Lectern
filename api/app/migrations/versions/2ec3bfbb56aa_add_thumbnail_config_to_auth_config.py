"""add thumbnail config to auth_config

Revision ID: 2ec3bfbb56aa
Revises: bf6010220cbf
Create Date: 2026-04-17 19:24:44.318172

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2ec3bfbb56aa"
down_revision: str | None = "bf6010220cbf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("thumbnail_quality", sa.Integer(), nullable=True))
    op.add_column("auth_configs", sa.Column("thumbnail_size_px", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "thumbnail_size_px")
    op.drop_column("auth_configs", "thumbnail_quality")
