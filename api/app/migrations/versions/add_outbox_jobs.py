"""add durable post-commit outbox jobs

Revision ID: add_outbox_jobs
Revises: e27745a4e006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "add_outbox_jobs"
down_revision: str | None = "e27745a4e006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_jobs",
        sa.Column("job_name", sa.String(length=100), nullable=False),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            primary_key=True,
        ),
    )
    op.create_index("ix_outbox_jobs_created_at", "outbox_jobs", ["created_at"])
    op.create_index("ix_outbox_jobs_delivered_at", "outbox_jobs", ["delivered_at"])
    op.create_index("ix_outbox_jobs_next_attempt_at", "outbox_jobs", ["next_attempt_at"])
    op.create_index("ix_outbox_jobs_abandoned_at", "outbox_jobs", ["abandoned_at"])
    op.create_table(
        "cas_staging_claims",
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            primary_key=True,
        ),
    )
    op.create_index("ix_cas_staging_claims_user_id", "cas_staging_claims", ["user_id"])
    op.create_index("ix_cas_staging_claims_file_key", "cas_staging_claims", ["file_key"])
    op.create_index("ix_cas_staging_claims_expires_at", "cas_staging_claims", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_cas_staging_claims_expires_at", table_name="cas_staging_claims")
    op.drop_index("ix_cas_staging_claims_file_key", table_name="cas_staging_claims")
    op.drop_index("ix_cas_staging_claims_user_id", table_name="cas_staging_claims")
    op.drop_table("cas_staging_claims")
    op.drop_index("ix_outbox_jobs_abandoned_at", table_name="outbox_jobs")
    op.drop_index("ix_outbox_jobs_next_attempt_at", table_name="outbox_jobs")
    op.drop_index("ix_outbox_jobs_delivered_at", table_name="outbox_jobs")
    op.drop_index("ix_outbox_jobs_created_at", table_name="outbox_jobs")
    op.drop_table("outbox_jobs")
