"""add_missing_userrole_values

Revision ID: ec44e6b72a9a
Revises: 0848234829d9
Create Date: 2026-04-10 14:25:38.231264

"""

from collections.abc import Sequence

from alembic import op

revision: str = "ec44e6b72a9a"
down_revision: str | None = "0848234829d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'moderator'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'bureau'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'vieux'")


def downgrade() -> None:
    pass
