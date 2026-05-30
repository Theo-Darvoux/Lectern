"""add directory_id to featured_items and make material_id nullable

Revision ID: 014_add_directory_id_to_featured
Revises: 013_add_email_sender_fields
Create Date: 2026-05-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "014_add_directory_id_to_featured"
down_revision: str | None = "013_add_email_sender_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Make material_id nullable
    op.alter_column("featured_items", "material_id", existing_type=sa.UUID(), nullable=True)
    # Add directory_id column
    op.add_column("featured_items", sa.Column("directory_id", sa.UUID(), nullable=True))
    # Add foreign key constraint for directory_id
    op.create_foreign_key(
        "fk_featured_items_directory",
        "featured_items",
        "directories",
        ["directory_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Drop foreign key constraint
    op.drop_constraint("fk_featured_items_directory", "featured_items", type_="foreignkey")
    # Drop directory_id column
    op.drop_column("featured_items", "directory_id")
    # Make material_id non-nullable again
    op.alter_column("featured_items", "material_id", existing_type=sa.UUID(), nullable=False)
