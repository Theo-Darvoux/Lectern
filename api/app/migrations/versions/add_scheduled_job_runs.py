"""add durable scheduled-job run identities

Revision ID: add_scheduled_job_runs
Revises: unique_reverts_pr_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_scheduled_job_runs"
down_revision: str | None = "unique_reverts_pr_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_job_runs",
        sa.Column("job_name", sa.String(length=100), nullable=False),
        sa.Column("run_key", sa.String(length=100), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_name", "run_key"),
    )


def downgrade() -> None:
    op.drop_table("scheduled_job_runs")
