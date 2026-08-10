"""add durable per-user authentication generation

Revision ID: add_user_auth_generation
Revises: add_installation_state
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_user_auth_generation"
down_revision: str | None = "add_installation_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Generation zero deliberately accepts credentials minted before this migration.
    # Security-sensitive offline recovery increments the value, invalidating every
    # access/refresh/browser token issued against an older generation.
    op.add_column(
        "users",
        sa.Column("auth_generation", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "auth_generation")
