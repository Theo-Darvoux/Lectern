"""merge heads

Revision ID: e8687f105994
Revises: c3d4e5f6a7b8, c9d0e1f2a3b4
Create Date: 2026-04-26 19:52:07.388224

"""

from collections.abc import Sequence

revision: str = "e8687f105994"
down_revision: str | tuple[str, ...] | None = ("c3d4e5f6a7b8", "c9d0e1f2a3b4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
