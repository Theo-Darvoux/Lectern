"""Rename QCM MIME type from vnd.wikint to vnd.lectern

Revision ID: rename_qcm_mime_type
Revises: add_thumbnail_status
Create Date: 2026-06-08

"""

import sqlalchemy as sa
from alembic import op

revision: str = "rename_qcm_mime_type"
down_revision: str = "rename_guest_email"
branch_labels = None
depends_on = None

OLD_MIME = "application/vnd.wikint.qcm+json"
NEW_MIME = "application/vnd.lectern.qcm+json"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE material_versions SET file_mime_type = :new WHERE file_mime_type = :old"
        ).bindparams(new=NEW_MIME, old=OLD_MIME)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE material_versions SET file_mime_type = :old WHERE file_mime_type = :new"
        ).bindparams(old=OLD_MIME, new=NEW_MIME)
    )
