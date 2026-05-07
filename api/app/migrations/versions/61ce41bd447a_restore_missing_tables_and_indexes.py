"""restore missing tables and indexes

Revision ID: 61ce41bd447a
Revises: 1cc078641ac3
Create Date: 2026-04-20 23:22:37.855364

"""

from collections.abc import Sequence

from alembic import op

revision: str = "61ce41bd447a"
down_revision: str | None = "1cc078641ac3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Restore uploads table
    op.execute("""
        CREATE TABLE IF NOT EXISTS uploads (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            upload_id VARCHAR(64) NOT NULL UNIQUE,
            user_id UUID,
            quarantine_key TEXT,
            final_key TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            filename VARCHAR(500) NOT NULL,
            mime_type VARCHAR(200),
            size_bytes BIGINT,
            sha256 VARCHAR(64),
            content_sha256 VARCHAR(64),
            error_detail TEXT,
            webhook_url TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            pipeline_stage INTEGER NOT NULL DEFAULT 0,
            cas_key VARCHAR(128),
            cas_ref_count INTEGER NOT NULL DEFAULT 0,
            thumbnail_key VARCHAR(500)
        )
    """)

    # 2. Restore missing indexes using IF NOT EXISTS
    # Uploads
    op.execute("CREATE INDEX IF NOT EXISTS ix_uploads_user_status ON uploads (user_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_uploads_sha256 ON uploads (sha256)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_uploads_content_sha256 ON uploads (content_sha256)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_uploads_cas_key ON uploads (cas_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_uploads_upload_id ON uploads (upload_id)")

    # Annotations
    op.execute("CREATE INDEX IF NOT EXISTS idx_annotations_author ON annotations (author_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_annotations_material ON annotations (material_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_annotations_thread ON annotations (thread_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_annotations_version ON annotations (version_id)")

    # Directories
    op.execute("CREATE INDEX IF NOT EXISTS idx_directories_parent ON directories (parent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_directories_slug ON directories (slug)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_directories_type ON directories (type)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_directories_deleted_at ON directories (deleted_at) WHERE deleted_at IS NOT NULL"
    )

    # Featured items
    op.execute("CREATE INDEX IF NOT EXISTS ix_featured_items_priority ON featured_items (priority)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_featured_items_window ON featured_items (start_at, end_at)"
    )

    # Material versions
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_versions_author ON material_versions (author_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_versions_material ON material_versions (material_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_material_versions_deleted_at ON material_versions (deleted_at) WHERE deleted_at IS NOT NULL"
    )

    # Materials
    op.execute("CREATE INDEX IF NOT EXISTS idx_materials_author ON materials (author_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_materials_directory ON materials (directory_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_materials_parent_material ON materials (parent_material_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_materials_slug ON materials (slug)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_materials_type ON materials (type)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_materials_deleted_at ON materials (deleted_at) WHERE deleted_at IS NOT NULL"
    )

    # PR Comments
    op.execute("CREATE INDEX IF NOT EXISTS idx_pr_comments_parent ON pr_comments (parent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pr_comments_pr ON pr_comments (pr_id)")

    # Pull Requests
    op.execute("CREATE INDEX IF NOT EXISTS idx_pull_requests_author ON pull_requests (author_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pull_requests_status ON pull_requests (status)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pull_requests_payload_gin ON pull_requests USING GIN (payload)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pull_requests_reverts_pr_id ON pull_requests (reverts_pr_id) WHERE reverts_pr_id IS NOT NULL"
    )

    # Tags
    op.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags (name)")

    # Users
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_deleted_at ON users (deleted_at)")


def downgrade() -> None:
    # No-op: we don't want to drop these again if we roll back
    pass
