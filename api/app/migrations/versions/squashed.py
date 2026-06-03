"""squashed initial migration

Revision ID: squashed
Revises:
Create Date: 2026-05-31

Replaces all previous migrations with a single authoritative schema snapshot.
Data seeds included: guest user, founding allowed domains.

To adopt this on an existing database that was at the old head:
    alembic stamp squashed

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "squashed"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --------------------------------------------------------------------- #
    # ENUM types                                                              #
    # --------------------------------------------------------------------- #
    userrole = postgresql.ENUM(
        "pending",
        "student",
        "moderator",
        "bureau",
        "vieux",
        "guest",
        name="userrole",
        create_type=False,
    )
    directorytype = postgresql.ENUM("module", "folder", name="directorytype", create_type=False)
    prstatus = postgresql.ENUM(
        "open",
        "approved",
        "rejected",
        "cancelled",
        name="prstatus",
        create_type=False,
    )
    flagstatus = postgresql.ENUM(
        "open",
        "reviewing",
        "resolved",
        "dismissed",
        name="flagstatus",
        create_type=False,
    )

    for e in (userrole, directorytype, prstatus, flagstatus):
        e.create(op.get_bind(), checkfirst=True)

    # --------------------------------------------------------------------- #
    # users                                                                   #
    # --------------------------------------------------------------------- #
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("role", userrole, server_default="student", nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("academic_year", sa.String(10), nullable=True),
        sa.Column("gdpr_consent", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("gdpr_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("onboarded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_flagged", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("auto_approve", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("idx_users_deleted_at", "users", ["deleted_at"])

    # --------------------------------------------------------------------- #
    # tags                                                                    #
    # --------------------------------------------------------------------- #
    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(50), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("idx_tags_name", "tags", ["name"])

    # --------------------------------------------------------------------- #
    # directories                                                             #
    # --------------------------------------------------------------------- #
    op.create_table(
        "directories",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("type", directorytype, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("like_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["directories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_directories_parent", "directories", ["parent_id"])
    op.create_index("idx_directories_slug", "directories", ["slug"])
    op.create_index("idx_directories_type", "directories", ["type"])
    op.create_index(
        "ix_directories_deleted_at",
        "directories",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.create_index(
        "uq_directory_parent_slug",
        "directories",
        ["parent_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --------------------------------------------------------------------- #
    # directory_tags                                                          #
    # --------------------------------------------------------------------- #
    op.create_table(
        "directory_tags",
        sa.Column("directory_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["directory_id"], ["directories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("directory_id", "tag_id"),
    )

    # --------------------------------------------------------------------- #
    # pull_requests                                                           #
    # --------------------------------------------------------------------- #
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("type", sa.String(50), server_default="batch", nullable=False),
        sa.Column("status", prstatus, server_default="open", nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("applied_result", postgresql.JSONB(), nullable=True),
        sa.Column("summary_types", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("virus_scan_result", sa.String(20), server_default="pending", nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("auto_merge_pending", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverts_pr_id", sa.Uuid(), nullable=True),
        sa.Column("reverted_by_pr_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["reverts_pr_id"],
            ["pull_requests.id"],
            name="fk_pull_requests_reverts_pr_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reverted_by_pr_id"],
            ["pull_requests.id"],
            name="fk_pull_requests_reverted_by_pr_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pull_requests_status", "pull_requests", ["status"])
    op.create_index("idx_pull_requests_author", "pull_requests", ["author_id"])
    op.create_index("idx_pull_requests_type_status", "pull_requests", ["type", "status"])
    op.create_index(
        "ix_pull_requests_payload_gin",
        "pull_requests",
        ["payload"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_pull_requests_reverts_pr_id",
        "pull_requests",
        ["reverts_pr_id"],
        postgresql_where=sa.text("reverts_pr_id IS NOT NULL"),
    )

    # --------------------------------------------------------------------- #
    # materials                                                               #
    # --------------------------------------------------------------------- #
    op.create_table(
        "materials",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("directory_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("slug", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("parent_material_id", sa.Uuid(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("download_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_views", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("views_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("views_14d", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "last_view_reset",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("like_count", sa.Integer(), server_default="0", nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["directory_id"], ["directories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_materials_directory", "materials", ["directory_id"])
    op.create_index("idx_materials_type", "materials", ["type"])
    op.create_index("idx_materials_author", "materials", ["author_id"])
    op.create_index("idx_materials_parent_material", "materials", ["parent_material_id"])
    op.create_index("idx_materials_slug", "materials", ["slug"])
    op.create_index(
        "ix_materials_deleted_at",
        "materials",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
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

    # --------------------------------------------------------------------- #
    # material_tags                                                           #
    # --------------------------------------------------------------------- #
    op.create_table(
        "material_tags",
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("material_id", "tag_id"),
    )

    # --------------------------------------------------------------------- #
    # material_versions                                                       #
    # --------------------------------------------------------------------- #
    op.create_table(
        "material_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("file_key", sa.String(500), nullable=True),
        sa.Column("file_name", sa.String(300), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_mime_type", sa.String(100), nullable=True),
        sa.Column("diff_summary", sa.Text(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("pr_id", sa.Uuid(), nullable=True),
        sa.Column("version_lock", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cas_sha256", sa.String(64), nullable=True),
        sa.Column("thumbnail_key", sa.String(500), nullable=True),
        sa.Column("virus_scan_result", sa.String(20), server_default="pending", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("material_id", "version_number", name="uq_material_version"),
    )
    op.create_index("idx_material_versions_material", "material_versions", ["material_id"])
    op.create_index("idx_material_versions_author", "material_versions", ["author_id"])
    op.create_index(
        "ix_material_versions_deleted_at",
        "material_versions",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )

    # --------------------------------------------------------------------- #
    # pr_file_claims                                                          #
    # --------------------------------------------------------------------- #
    op.create_table(
        "pr_file_claims",
        sa.Column("file_key", sa.Text(), nullable=False),
        sa.Column("pr_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("file_key"),
    )

    # --------------------------------------------------------------------- #
    # pr_comments                                                             #
    # --------------------------------------------------------------------- #
    op.create_table(
        "pr_comments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("pr_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_id"], ["pr_comments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pr_comments_pr", "pr_comments", ["pr_id"])
    op.create_index("idx_pr_comments_parent", "pr_comments", ["parent_id"])

    # --------------------------------------------------------------------- #
    # comments                                                                #
    # --------------------------------------------------------------------- #
    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_comments_target", "comments", ["target_type", "target_id", "created_at"])

    # --------------------------------------------------------------------- #
    # annotations                                                             #
    # --------------------------------------------------------------------- #
    op.create_table(
        "annotations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("selection_text", sa.Text(), nullable=True),
        sa.Column("position_data", postgresql.JSONB(), nullable=True),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("reply_to_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["material_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["annotations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reply_to_id"], ["annotations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_annotations_material", "annotations", ["material_id"])
    op.create_index("idx_annotations_thread", "annotations", ["thread_id"])
    op.create_index("idx_annotations_version", "annotations", ["version_id"])
    op.create_index("idx_annotations_author", "annotations", ["author_id"])

    # --------------------------------------------------------------------- #
    # view_history                                                            #
    # --------------------------------------------------------------------- #
    op.create_table(
        "view_history",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column(
            "viewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "material_id", name="uq_view_history_user_material"),
    )
    op.create_index("idx_view_history_user", "view_history", ["user_id", "viewed_at"])

    # --------------------------------------------------------------------- #
    # flags                                                                   #
    # --------------------------------------------------------------------- #
    op.create_table(
        "flags",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", flagstatus, server_default="open", nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reporter_id", "target_type", "target_id", name="uq_flag_reporter_target"
        ),
    )
    op.create_index("idx_flags_status", "flags", ["status"])
    op.create_index("idx_flags_target", "flags", ["target_type", "target_id"])

    # --------------------------------------------------------------------- #
    # notifications                                                           #
    # --------------------------------------------------------------------- #
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("read", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_notifications_user_unread", "notifications", ["user_id", "read", "created_at"]
    )

    # --------------------------------------------------------------------- #
    # download_audit                                                          #
    # --------------------------------------------------------------------- #
    op.create_table(
        "download_audit",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_download_audit_user_id", "download_audit", ["user_id"])
    op.create_index("ix_download_audit_material_id", "download_audit", ["material_id"])
    op.create_index("ix_download_audit_created_at", "download_audit", ["created_at"])

    # --------------------------------------------------------------------- #
    # uploads                                                                 #
    # --------------------------------------------------------------------- #
    op.create_table(
        "uploads",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("upload_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("quarantine_key", sa.Text(), nullable=True),
        sa.Column("final_key", sa.Text(), nullable=True),
        sa.Column("thumbnail_key", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(200), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("pipeline_stage", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cas_key", sa.String(128), nullable=True),
        sa.Column("cas_ref_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processing_status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("webhook_url", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("upload_id"),
    )
    op.create_index("ix_uploads_user_status", "uploads", ["user_id", "status"])
    op.create_index("ix_uploads_sha256", "uploads", ["sha256"])
    op.create_index("ix_uploads_content_sha256", "uploads", ["content_sha256"])
    op.create_index("ix_uploads_upload_id", "uploads", ["upload_id"], unique=True)
    op.create_index("ix_uploads_cas_key", "uploads", ["cas_key"])
    op.create_index(
        "ix_uploads_sha256_clean",
        "uploads",
        ["sha256"],
        postgresql_where=sa.text("status = 'clean'"),
    )

    # --------------------------------------------------------------------- #
    # dead_letter_jobs                                                        #
    # --------------------------------------------------------------------- #
    op.create_table(
        "dead_letter_jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_name", sa.String(100), nullable=False),
        sa.Column("upload_id", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_letter_jobs_upload_id", "dead_letter_jobs", ["upload_id"])

    # --------------------------------------------------------------------- #
    # featured_items                                                          #
    # --------------------------------------------------------------------- #
    op.create_table(
        "featured_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("directory_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["directory_id"], ["directories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_featured_items_window", "featured_items", ["start_at", "end_at"])
    op.create_index("ix_featured_items_priority", "featured_items", ["priority"])

    # --------------------------------------------------------------------- #
    # allowed_domains                                                         #
    # --------------------------------------------------------------------- #
    op.create_table(
        "allowed_domains",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("auto_approve", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", name="uq_allowed_domains_domain"),
    )

    # --------------------------------------------------------------------- #
    # material_likes / material_favourites                                    #
    # --------------------------------------------------------------------- #
    op.create_table(
        "material_likes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "material_id", name="uq_material_like_user_material"),
    )

    op.create_table(
        "material_favourites",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "material_id", name="uq_material_favourite_user_material"),
    )

    # --------------------------------------------------------------------- #
    # directory_likes / directory_favourites                                  #
    # --------------------------------------------------------------------- #
    op.create_table(
        "directory_likes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("directory_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["directory_id"], ["directories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "directory_id", name="uq_directory_like_user_directory"),
    )

    op.create_table(
        "directory_favourites",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("directory_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["directory_id"], ["directories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "directory_id", name="uq_directory_favourite_user_directory"
        ),
    )

    # --------------------------------------------------------------------- #
    # Views                                                                   #
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE VIEW user_stats AS
        SELECT u.id AS user_id,
               (SELECT COUNT(*) FROM pull_requests WHERE author_id = u.id AND status = 'approved') AS prs_approved,
               (SELECT COUNT(*) FROM pull_requests WHERE author_id = u.id)                          AS prs_total,
               (SELECT COUNT(*) FROM annotations WHERE author_id = u.id)                            AS annotations_count,
               (SELECT COUNT(*) FROM comments WHERE author_id = u.id)                               AS comments_count,
               (SELECT COUNT(*) FROM pull_requests WHERE author_id = u.id AND status = 'open')      AS open_pr_count
        FROM users u
    """)

    # --------------------------------------------------------------------- #
    # Data seeds                                                              #
    # --------------------------------------------------------------------- #
    op.execute(
        "INSERT INTO users (id, email, display_name, role, onboarded, gdpr_consent) "
        "VALUES (gen_random_uuid(), 'guest@wikint.local', 'Guest', 'guest', true, true) "
        "ON CONFLICT (email) DO NOTHING"
    )

    op.execute(
        "INSERT INTO allowed_domains (id, domain, auto_approve) VALUES "
        "(gen_random_uuid(), 'telecom-sudparis.eu', true), "
        "(gen_random_uuid(), 'imt-bs.eu', true) "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS user_stats")

    op.drop_table("directory_favourites")
    op.drop_table("directory_likes")
    op.drop_table("material_favourites")
    op.drop_table("material_likes")
    op.drop_table("allowed_domains")
    op.drop_table("featured_items")
    op.drop_table("dead_letter_jobs")
    op.drop_table("uploads")
    op.drop_table("download_audit")
    op.drop_table("notifications")
    op.drop_table("flags")
    op.drop_table("view_history")
    op.drop_table("annotations")
    op.drop_table("comments")
    op.drop_table("pr_comments")
    op.drop_table("pr_file_claims")
    op.drop_table("material_versions")
    op.drop_table("material_tags")
    op.drop_table("materials")
    op.drop_table("pull_requests")
    op.drop_table("directory_tags")
    op.drop_table("directories")
    op.drop_table("tags")
    op.drop_table("users")

    for name in ("userrole", "directorytype", "prstatus", "flagstatus"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
