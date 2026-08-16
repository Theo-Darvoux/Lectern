"""add material content status

Revision ID: add_material_content_status
Revises: add_cas_staging_claim_size
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_material_content_status"
down_revision: str | None = "add_cas_staging_claim_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED = "status IN ('important', 'current', 'deprecated', 'archived')"


def upgrade() -> None:
    op.add_column(
        "materials",
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="current",
            nullable=False,
        ),
    )
    op.create_check_constraint("ck_materials_content_status", "materials", _ALLOWED)
    op.create_index("ix_materials_status", "materials", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_materials_status", table_name="materials")
    op.drop_constraint("ck_materials_content_status", "materials", type_="check")
    op.drop_column("materials", "status")
