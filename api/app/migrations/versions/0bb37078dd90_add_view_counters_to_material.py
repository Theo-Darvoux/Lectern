"""add_view_counters_to_material

Revision ID: 0bb37078dd90
Revises: b4c8deec8f6b
Create Date: 2026-04-10 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0bb37078dd90"
down_revision: str | None = "a1b2c3d4e5f6"  # Adjusted to the latest migration I see in docs
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Adding total_views and views_today to materials table
    op.add_column(
        "materials", sa.Column("total_views", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.add_column(
        "materials", sa.Column("views_today", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "materials",
        sa.Column(
            "last_view_reset",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("materials", "last_view_reset")
    op.drop_column("materials", "views_today")
    op.drop_column("materials", "total_views")
