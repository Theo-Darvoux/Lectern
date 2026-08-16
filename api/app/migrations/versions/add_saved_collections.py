"""add named saved collections

Revision ID: add_saved_collections
Revises: add_cas_staging_claim_size
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_saved_collections"
down_revision: str | None = "add_cas_staging_claim_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("name_key", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name_key", name="uq_collection_user_name_key"),
    )
    op.create_index("ix_collections_user_id", "collections", ["user_id"], unique=False)

    op.create_table(
        "collection_items",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("directory_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(material_id IS NOT NULL AND directory_id IS NULL) OR "
            "(material_id IS NULL AND directory_id IS NOT NULL)",
            name="ck_collection_item_exactly_one_target",
        ),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["directory_id"], ["directories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_id", "directory_id", name="uq_collection_item_collection_directory"
        ),
        sa.UniqueConstraint(
            "collection_id", "material_id", name="uq_collection_item_collection_material"
        ),
    )
    op.create_index(
        "ix_collection_items_collection_id", "collection_items", ["collection_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_collection_items_collection_id", table_name="collection_items")
    op.drop_table("collection_items")
    op.drop_index("ix_collections_user_id", table_name="collections")
    op.drop_table("collections")
