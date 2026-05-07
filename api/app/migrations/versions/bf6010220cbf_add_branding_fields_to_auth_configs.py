"""add branding fields to auth_configs

Revision ID: bf6010220cbf
Revises: 15a907a42e43
Create Date: 2026-04-17 19:09:14.782965

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bf6010220cbf"
down_revision: str | None = "15a907a42e43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("site_name", sa.String(length=100), nullable=True))
    op.add_column("auth_configs", sa.Column("site_description", sa.Text(), nullable=True))
    op.add_column("auth_configs", sa.Column("site_logo_url", sa.String(length=255), nullable=True))
    op.add_column(
        "auth_configs", sa.Column("site_favicon_url", sa.String(length=255), nullable=True)
    )
    op.add_column("auth_configs", sa.Column("primary_color", sa.String(length=10), nullable=True))
    op.add_column("auth_configs", sa.Column("footer_text", sa.Text(), nullable=True))
    op.add_column(
        "auth_configs", sa.Column("organization_url", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("auth_configs", "organization_url")
    # Use lowercase sa.text() or just TEXT if using alembic's wrapped sa
    op.drop_column("auth_configs", "footer_text")
    op.drop_column("auth_configs", "primary_color")
    op.drop_column("auth_configs", "site_favicon_url")
    op.drop_column("auth_configs", "site_logo_url")
    op.drop_column("auth_configs", "site_description")
    op.drop_column("auth_configs", "site_name")
