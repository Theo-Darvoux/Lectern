"""Drop generated thumbnails of file types that no longer get one.

Text, code, Markdown and notebook files used to get a LibreOffice render of
their first page. They now show a generic card, so their thumbnail pointers are
cleared and marked "skipped". The WebP objects stay in storage until a staff
member removes them with the storage Integrity Check (staff System page).

The type list mirrors ``app.core.media.thumbnails.thumbnail_kind``. Rows with no
specific type (NULL, "" or application/octet-stream) are left alone, because the
renderer classified those by extension.

Revision ID: drop_text_thumbnails
Revises: strip_qcm_draft_metadata
"""

from collections.abc import Sequence

from alembic import op

revision: str = "drop_text_thumbnails"
down_revision: str | None = "strip_qcm_draft_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEPT_MIMES = (
    "application/pdf",
    "application/epub+zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "",
    "application/octet-stream",
)


def _clear(table: str, mime_column: str) -> None:
    mime = f"lower(trim(split_part(coalesce({mime_column}, ''), ';', 1)))"
    kept = ", ".join(f"'{value}'" for value in _KEPT_MIMES)
    op.execute(
        f"""
        UPDATE {table}
        SET thumbnail_key = NULL, thumbnail_status = 'skipped'
        WHERE thumbnail_key IS NOT NULL
          AND {mime} NOT LIKE 'image/%'
          AND {mime} NOT LIKE 'video/%'
          AND {mime} NOT LIKE 'audio/%'
          AND {mime} NOT IN ({kept})
        """
    )


def upgrade() -> None:
    _clear("material_versions", "file_mime_type")
    _clear("uploads", "mime_type")


def downgrade() -> None:
    # The cleared pointers cannot be restored. Run
    # `python -m app.cli recalculate-thumbnails --force` on the old code instead.
    pass
