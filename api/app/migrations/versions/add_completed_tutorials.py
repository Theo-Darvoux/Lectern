"""Add completed_tutorials JSON column to users

Revision ID: add_completed_tutorials
Revises: rename_qcm_mime_type
Create Date: 2026-06-08

"""

import sqlalchemy as sa
from alembic import op

revision: str = "add_completed_tutorials"
down_revision: str = "rename_qcm_mime_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "completed_tutorials",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "completed_tutorials")
