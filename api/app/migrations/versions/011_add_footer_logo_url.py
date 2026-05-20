"""add footer_logo_url to auth_configs

Revision ID: 011_add_footer_logo_url
Revises: fe2b420e9ac1
Create Date: 2026-05-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "011_add_footer_logo_url"
down_revision: tuple[str, str] = ("010", "fe2b420e9ac1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_configs", sa.Column("footer_logo_url", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("auth_configs", "footer_logo_url")
