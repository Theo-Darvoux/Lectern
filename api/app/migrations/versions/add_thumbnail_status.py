"""add thumbnail_status to uploads and material_versions

Revision ID: add_thumbnail_status
Revises: drop_system_directories
Create Date: 2026-06-04

"""

import sqlalchemy as sa
from alembic import op

revision: str = "add_thumbnail_status"
down_revision: str = "drop_system_directories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploads", sa.Column("thumbnail_status", sa.String(10), nullable=True))
    op.add_column("material_versions", sa.Column("thumbnail_status", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("material_versions", "thumbnail_status")
    op.drop_column("uploads", "thumbnail_status")
