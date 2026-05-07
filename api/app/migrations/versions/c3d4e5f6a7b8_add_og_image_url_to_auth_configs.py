"""add og_image_url to auth_configs

Revision ID: c3d4e5f6a7b8
Revises: fe2b420e9ac1
Create Date: 2026-04-26 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "fe2b420e9ac1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("og_image_url", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "og_image_url")
