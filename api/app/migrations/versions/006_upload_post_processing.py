"""Add processing_status to uploads and auto_merge_pending to pull_requests

Revision ID: 006
Revises: e8687f105994
Create Date: 2026-05-14

Changes:
- uploads.processing_status (VARCHAR 20, NOT NULL DEFAULT 'pending')
  Tracks post-scan background processing: pending → running → complete | degraded
  Existing clean uploads are backfilled to 'complete' (already fully processed).
- pull_requests.auto_merge_pending (BOOLEAN, NOT NULL DEFAULT FALSE)
  Defers auto-approval for moderator PRs until all files finish post-scan processing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision: str | None = "e8687f105994"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "uploads",
        sa.Column(
            "processing_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
    )
    # Existing clean uploads are fully processed — mark them complete.
    op.execute("UPDATE uploads SET processing_status = 'complete' WHERE status = 'clean'")

    op.add_column(
        "pull_requests",
        sa.Column(
            "auto_merge_pending",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("pull_requests", "auto_merge_pending")
    op.drop_column("uploads", "processing_status")
