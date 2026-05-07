"""add auto_approve_all_domains to auth_configs

Revision ID: b3c4d5e6f7a8
Revises: fe2b420e9ac1
Create Date: 2026-04-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "fe2b420e9ac1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_configs",
        sa.Column("auto_approve_all_domains", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("auth_configs", "auto_approve_all_domains")
