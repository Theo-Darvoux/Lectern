"""Add legal_siret to auth_configs

Revision ID: e3b56f8f7c9a
Revises: fe2b420e9ac1
Create Date: 2026-04-25 13:38:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3b56f8f7c9a"
down_revision: str | None = "fe2b420e9ac1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("legal_siret", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "legal_siret")
