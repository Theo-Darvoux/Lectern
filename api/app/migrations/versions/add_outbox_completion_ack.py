"""add durable completion acknowledgement for search deindex jobs

Revision ID: add_outbox_completion_ack
Revises: add_user_auth_generation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_outbox_completion_ack"
down_revision: str | None = "add_user_auth_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_jobs",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_jobs_completed_at", "outbox_jobs", ["completed_at"])

    # Rows created before this migration used delivered_at as queue acceptance,
    # not external completion. Re-open every retained deindex row so the new
    # worker can idempotently delete it and write a real completion ack.
    op.execute(
        sa.text(
            """
            UPDATE outbox_jobs
               SET abandoned_at = NULL,
                   next_attempt_at = CURRENT_TIMESTAMP
             WHERE job_name = 'delete_indexed_item'
               AND completed_at IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_jobs_completed_at", table_name="outbox_jobs")
    op.drop_column("outbox_jobs", "completed_at")
