"""merge heads: b3c4d5e6f7a8 and e3b56f8f7c9a

Revision ID: c9d0e1f2a3b4
Revises: b3c4d5e6f7a8, e3b56f8f7c9a
Create Date: 2026-04-26 00:00:00.000000

"""

from collections.abc import Sequence

revision: str = "c9d0e1f2a3b4"
down_revision: tuple[str, ...] = ("b3c4d5e6f7a8", "e3b56f8f7c9a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
