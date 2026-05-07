"""add password_hash to users

Revision ID: dfc717687b05
Revises: ef7d8e9f1a2b
Create Date: 2026-04-23 15:09:26.781068

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dfc717687b05"
down_revision: str | None = "ef7d8e9f1a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
