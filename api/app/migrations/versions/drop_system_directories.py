from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "drop_system_directories"
down_revision: str = "add_flag_reason_to_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Detach attachments from system directories so the cascade delete below
    # does not remove them — parent_material_id is now the sole attachment link.
    op.execute("UPDATE materials SET directory_id = NULL WHERE parent_material_id IS NOT NULL")
    # Remove all system directories (attachments:{material_id} folders).
    op.execute("DELETE FROM directories WHERE is_system = TRUE")
    op.drop_column("directories", "is_system")


def downgrade() -> None:
    op.add_column(
        "directories",
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
    )
