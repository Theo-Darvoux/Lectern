"""merge_heads

Revision ID: 9a8b7c6d5e4f
Revises: 0bb37078dd90, ec44e6b72a9a
Create Date: 2026-04-10 15:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "9a8b7c6d5e4f"
down_revision: str | None | tuple[str, ...] = ("0bb37078dd90", "ec44e6b72a9a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
