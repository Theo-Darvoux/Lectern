"""add flag_reason to users

Revision ID: add_flag_reason_to_users
Revises: squashed
Create Date: 2026-06-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_flag_reason_to_users"
down_revision: str = "squashed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("flag_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "flag_reason")
