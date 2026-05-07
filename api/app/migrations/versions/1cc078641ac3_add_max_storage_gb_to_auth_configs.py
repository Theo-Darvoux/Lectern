"""add max_storage_gb to auth_configs

Revision ID: 1cc078641ac3
Revises: 2ec3bfbb56aa
Create Date: 2026-04-20 11:30:33.939605

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1cc078641ac3"
down_revision: str | None = "2ec3bfbb56aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("max_storage_gb", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "max_storage_gb")
