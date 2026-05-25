"""add smtp_sender_name and smtp_avatar_url to auth_configs

Revision ID: 013_add_email_sender_fields
Revises: 012_add_guest_access
Create Date: 2026-05-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "013_add_email_sender_fields"
down_revision: str | None = "012_add_guest_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_configs", sa.Column("smtp_sender_name", sa.String(length=100), nullable=True))
    op.add_column("auth_configs", sa.Column("smtp_avatar_url", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_configs", "smtp_avatar_url")
    op.drop_column("auth_configs", "smtp_sender_name")
