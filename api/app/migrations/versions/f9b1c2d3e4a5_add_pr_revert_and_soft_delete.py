"""add PR revert support and soft-delete columns

Revision ID: f9b1c2d3e4a5
Revises: a7b3c9d2e4f5
Create Date: 2026-04-16 12:00:00.000000

- pull_requests.approved_at: timestamp when the PR was materialized. Anchors
  the 7-day revert grace period. Backfilled from updated_at for existing
  APPROVED rows.
- pull_requests.reverts_pr_id: self-FK, set on revert PRs pointing to the
  original PR they undo.
- pull_requests.reverted_by_pr_id: self-FK on the original PR, set when a
  revert PR has been applied against it. Mutually exclusive lifecycle with
  reverts_pr_id on the same row.
- materials/directories/material_versions.deleted_at: nullable timestamp.
  NULL = live row. Non-NULL = soft-deleted, eligible for hard-delete by the
  cleanup worker once older than the grace period.
- Unique slug constraints become partial indexes filtered on deleted_at IS NULL
  so that hard-deleted tombstones do not block new slugs in the same directory.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


def index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    indexes = insp.get_indexes(table_name)
    return any(idx["name"] == index_name for idx in indexes)


revision: str = "f9b1c2d3e4a5"
down_revision: str | None = "a7b3c9d2e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- pull_requests: revert tracking ---
    op.add_column(
        "pull_requests",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pull_requests",
        sa.Column("reverts_pr_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "pull_requests",
        sa.Column("reverted_by_pr_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_pull_requests_reverts_pr_id",
        "pull_requests",
        "pull_requests",
        ["reverts_pr_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_pull_requests_reverted_by_pr_id",
        "pull_requests",
        "pull_requests",
        ["reverted_by_pr_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_pull_requests_reverts_pr_id",
        "pull_requests",
        ["reverts_pr_id"],
        postgresql_where=sa.text("reverts_pr_id IS NOT NULL"),
    )

    # Backfill approved_at for existing APPROVED rows. They will all be past
    # the 7-day grace window by design (legacy PRs are non-revertable anyway
    # because they lack pre_state snapshots, enforced at the service layer).
    op.execute("UPDATE pull_requests SET approved_at = updated_at WHERE status = 'approved'")

    # --- soft-delete columns ---
    for table in ("materials", "directories", "material_versions"):
        op.add_column(
            table,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            f"ix_{table}_deleted_at",
            table,
            ["deleted_at"],
            postgresql_where=sa.text("deleted_at IS NOT NULL"),
        )

    # --- rebuild slug uniqueness as partial (live rows only) ---
    # materials: drop old constraint + root-slug partial index, add two new
    # partial indexes that also exclude soft-deleted rows.
    op.drop_constraint("uq_material_directory_slug", "materials", type_="unique")
    if index_exists("materials", "uq_material_root_slug"):
        op.drop_index("uq_material_root_slug", table_name="materials")
    op.create_index(
        "uq_material_directory_slug",
        "materials",
        ["directory_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_material_root_slug",
        "materials",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("directory_id IS NULL AND deleted_at IS NULL"),
    )

    # directories: same treatment for parent_id+slug.
    op.drop_constraint("uq_directory_parent_slug", "directories", type_="unique")
    op.create_index(
        "uq_directory_parent_slug",
        "directories",
        ["parent_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    # directories: revert to plain unique constraint.
    op.drop_index("uq_directory_parent_slug", table_name="directories")
    op.create_unique_constraint("uq_directory_parent_slug", "directories", ["parent_id", "slug"])

    # materials: revert to plain unique + original root-slug partial index.
    if index_exists("materials", "uq_material_root_slug"):
        op.drop_index("uq_material_root_slug", table_name="materials")
    op.drop_index("uq_material_directory_slug", table_name="materials")
    op.create_unique_constraint("uq_material_directory_slug", "materials", ["directory_id", "slug"])
    op.create_index(
        "uq_material_root_slug",
        "materials",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("directory_id IS NULL"),
    )

    # Drop soft-delete columns.
    for table in ("material_versions", "directories", "materials"):
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        op.drop_column(table, "deleted_at")

    # Drop PR revert columns.
    op.drop_index("ix_pull_requests_reverts_pr_id", table_name="pull_requests")
    op.drop_constraint("fk_pull_requests_reverted_by_pr_id", "pull_requests", type_="foreignkey")
    op.drop_constraint("fk_pull_requests_reverts_pr_id", "pull_requests", type_="foreignkey")
    op.drop_column("pull_requests", "reverted_by_pr_id")
    op.drop_column("pull_requests", "reverts_pr_id")
    op.drop_column("pull_requests", "approved_at")
