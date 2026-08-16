"""add directory content status

Revision ID: add_directory_status
Revises: add_material_content_status
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_directory_status"
down_revision: str | None = "add_material_content_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED = "status IN ('important', 'current', 'deprecated', 'archived')"


def upgrade() -> None:
    op.add_column(
        "directories",
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="current",
            nullable=False,
        ),
    )
    op.create_check_constraint("ck_directories_status", "directories", _ALLOWED)
    op.create_index("ix_directories_status", "directories", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_directories_status", table_name="directories")
    op.drop_constraint("ck_directories_status", "directories", type_="check")
    op.drop_column("directories", "status")
