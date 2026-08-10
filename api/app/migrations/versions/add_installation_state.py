"""add durable one-way installation bootstrap state

Revision ID: add_installation_state
Revises: add_scheduled_job_runs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_installation_state"
down_revision: str | None = "add_scheduled_job_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "installation_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column(
            "bootstrapped_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_installation_state_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Existing initialized deployments must never re-enter HTTP bootstrap after
    # upgrade. Fresh databases with no live admin intentionally retain no row.
    op.execute(
        sa.text(
            "INSERT INTO installation_state (id) "
            "SELECT 1 WHERE EXISTS ("
            "SELECT 1 FROM users "
            "WHERE role IN ('bureau', 'vieux') AND deleted_at IS NULL"
            ")"
        )
    )


def downgrade() -> None:
    op.drop_table("installation_state")
