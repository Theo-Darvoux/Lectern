"""enforce live root-directory slug uniqueness

Revision ID: add_directory_root_slug_unique
Revises: add_outbox_completion_ack
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_directory_root_slug_unique"
down_revision: str | None = "add_outbox_completion_ack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            """
            SELECT slug, COUNT(*) AS duplicate_count
              FROM directories
             WHERE parent_id IS NULL
               AND deleted_at IS NULL
             GROUP BY slug
            HAVING COUNT(*) > 1
             ORDER BY slug
             LIMIT 10
            """
        )
    ).all()
    if duplicates:
        detail = ", ".join(f"{row.slug!r} ({row.duplicate_count})" for row in duplicates)
        raise RuntimeError(
            "Cannot enforce root-directory slug uniqueness while duplicate live root "
            f"slugs exist. Resolve them first: {detail}"
        )

    op.create_index(
        "uq_directory_root_slug",
        "directories",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL AND deleted_at IS NULL"),
        sqlite_where=sa.text("parent_id IS NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_directory_root_slug", table_name="directories")
