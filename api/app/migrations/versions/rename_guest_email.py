"""rename the seeded guest account email guest@wikint.local -> guest@lectern.local

The guest user is identified by role ('guest'), so its email is cosmetic. This
migration updates instances that were seeded before the rename. Fresh installs
already seed the new email in the squashed baseline, so this is a no-op there.

Revision ID: rename_guest_email
Revises: add_thumbnail_status
Create Date: 2026-06-08

"""

from alembic import op

revision: str = "rename_guest_email"
down_revision: str = "add_thumbnail_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET email = 'guest@lectern.local' "
        "WHERE role = 'guest' AND email = 'guest@wikint.local'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE users SET email = 'guest@wikint.local' "
        "WHERE role = 'guest' AND email = 'guest@lectern.local'"
    )
