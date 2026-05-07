"""add smtp ip to auth configs

Revision ID: ef7d8e9f1a2b
Revises: 61ce41bd447a
Create Date: 2026-04-23 04:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef7d8e9f1a2b"
down_revision: str | None = "61ce41bd447a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("smtp_ip", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "smtp_ip")
