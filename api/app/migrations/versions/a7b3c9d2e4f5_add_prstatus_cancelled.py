"""add cancelled value to prstatus enum

Revision ID: a7b3c9d2e4f5
Revises: c17ff13b39e9
Create Date: 2026-04-16 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7b3c9d2e4f5"
down_revision: str | None = "c17ff13b39e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE prstatus ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a full type rebuild.
    pass
