"""add site_name_style to auth_configs

Revision ID: 008
Revises: 007
Create Date: 2026-05-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("site_name_style", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "site_name_style")
