"""add guest access toggle, guest role, and seeded guest user

Revision ID: 012_add_guest_access
Revises: 011_add_footer_logo_url
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "012_add_guest_access"
down_revision: str | None = "011_add_footer_logo_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add 'guest' to the userrole enum (must be committed outside the
    #    migration transaction before it can be referenced below).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'guest'")

    # 2. Admin toggle that gates guest browsing.
    op.add_column(
        "auth_configs",
        sa.Column(
            "guest_access_enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )

    # 3. Seed the single shared guest identity. It is pre-onboarded so the
    #    frontend never routes it through the onboarding flow, and has GDPR
    #    consent so it is never prompted. All guests share this row; they can
    #    never persist anything because writes are blocked server-side.
    op.execute(
        "INSERT INTO users (id, email, display_name, role, onboarded, gdpr_consent) "
        "VALUES (gen_random_uuid(), 'guest@wikint.local', 'Guest', 'guest', true, true) "
        "ON CONFLICT (email) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM users WHERE email = 'guest@wikint.local'")
    op.drop_column("auth_configs", "guest_access_enabled")
    # PostgreSQL does not support removing enum values; skip downgrade of userrole.
