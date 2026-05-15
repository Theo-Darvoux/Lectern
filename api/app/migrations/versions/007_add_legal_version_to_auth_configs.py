"""add legal_version to auth_configs

Revision ID: 007
Revises: 006
Create Date: 2026-05-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("legal_version", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "legal_version")
