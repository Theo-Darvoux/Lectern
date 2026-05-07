"""add_auth_config_and_pending_role

Revision ID: b2c3d4e5f6a7
Revises: f9b1c2d3e4a5
Create Date: 2026-04-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "f9b1c2d3e4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add 'pending' to the userrole enum (must be outside a transaction)
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'pending' BEFORE 'student'")

    # 2. Global auth configuration table (single row)
    op.create_table(
        "auth_configs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("totp_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("google_oauth_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("open_registration", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 3. Per-domain access policy table
    op.create_table(
        "allowed_domains",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("auto_approve", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("domain", name="uq_allowed_domains_domain"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 4. Seed: one config row + two founding domains
    op.execute(
        "INSERT INTO auth_configs (id, totp_enabled, google_oauth_enabled, open_registration) "
        "VALUES (gen_random_uuid(), true, false, false)"
    )
    op.execute(
        "INSERT INTO allowed_domains (id, domain, auto_approve) VALUES "
        "(gen_random_uuid(), 'telecom-sudparis.eu', true), "
        "(gen_random_uuid(), 'imt-bs.eu', true)"
    )


def downgrade() -> None:
    op.drop_table("allowed_domains")
    op.drop_table("auth_configs")
    # PostgreSQL does not support removing enum values; skip downgrade of userrole
