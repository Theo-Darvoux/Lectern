"""add_directory_interactions

Revision ID: f3a99f3cd139
Revises: bcffa032b26e
Create Date: 2026-04-11 01:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a99f3cd139"
down_revision: str | None = "bcffa032b26e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add like_count to directories
    op.add_column(
        "directories", sa.Column("like_count", sa.Integer(), server_default="0", nullable=False)
    )

    # Create directory_likes table
    op.create_table(
        "directory_likes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("directory_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["directory_id"], ["directories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "directory_id", name="uq_directory_like_user_directory"),
    )

    # Create directory_favourites table
    op.create_table(
        "directory_favourites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("directory_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["directory_id"], ["directories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "directory_id", name="uq_directory_favourite_user_directory"
        ),
    )


def downgrade() -> None:
    op.drop_table("directory_favourites")
    op.drop_table("directory_likes")
    op.drop_column("directories", "like_count")
