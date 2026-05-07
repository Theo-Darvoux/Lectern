"""add_likes_and_favourites

Revision ID: bcffa032b26e
Revises: 9a8b7c6d5e4f
Create Date: 2026-04-10 15:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bcffa032b26e"
down_revision: str | None = "9a8b7c6d5e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add like_count to materials
    op.add_column(
        "materials", sa.Column("like_count", sa.Integer(), server_default="0", nullable=False)
    )

    # Create material_likes table
    op.create_table(
        "material_likes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("material_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "material_id", name="uq_material_like_user_material"),
    )

    # Create material_favourites table
    op.create_table(
        "material_favourites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("material_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "material_id", name="uq_material_favourite_user_material"),
    )


def downgrade() -> None:
    op.drop_table("material_favourites")
    op.drop_table("material_likes")
    op.drop_column("materials", "like_count")
