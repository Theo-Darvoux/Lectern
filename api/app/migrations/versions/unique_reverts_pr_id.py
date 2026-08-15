"""enforce one revert per original pull request

Revision ID: unique_reverts_pr_id
Revises: add_outbox_jobs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "unique_reverts_pr_id"
down_revision: str | None = "add_outbox_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "uq_pull_requests_reverts_pr_id"


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT reverts_pr_id FROM pull_requests "
            "WHERE reverts_pr_id IS NOT NULL "
            "GROUP BY reverts_pr_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        duplicate_ids = ", ".join(str(row[0]) for row in duplicates)
        raise RuntimeError(
            "Cannot enforce unique PR reverts; duplicate history exists for: " + duplicate_ids
        )
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        "pull_requests",
        ["reverts_pr_id"],
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "pull_requests", type_="unique")
