"""Remove QCM draft copies that drafts leaked into metadata and PR payloads.

The draft used to carry the whole QCM under ``metadata.qcm_draft`` and sent it
with the contribution, so it ended up on materials and in pull requests.

Revision ID: strip_qcm_draft_metadata
Revises: add_anonymous_pull_requests
"""

from collections.abc import Sequence

from alembic import op

revision: str = "strip_qcm_draft_metadata"
down_revision: str | None = "add_anonymous_pull_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _clean_operations(column: str) -> None:
    # An edit with metadata {} would wipe the material's metadata on approval,
    # so an edit left with nothing else drops the field entirely.
    op.execute(
        f"""
        UPDATE pull_requests
        SET {column} = (
            SELECT jsonb_agg(
                CASE
                    WHEN cleaned->>'op' = 'edit_material' AND cleaned->'metadata' = '{{}}'::jsonb
                    THEN cleaned - 'metadata'
                    ELSE cleaned
                END
                ORDER BY position
            )
            FROM jsonb_array_elements({column}) WITH ORDINALITY AS t(item, position)
            CROSS JOIN LATERAL (
                SELECT item #- '{{metadata,qcm_draft}}' #- '{{pre_state,metadata,qcm_draft}}'
                    AS cleaned
            ) AS stripped
        )
        WHERE {column}::text LIKE '%qcm_draft%'
        """
    )


def upgrade() -> None:
    op.execute(
        """
        UPDATE materials
        SET metadata = metadata - 'qcm_draft'
        WHERE jsonb_exists(metadata, 'qcm_draft')
        """
    )
    _clean_operations("payload")
    _clean_operations("applied_result")


def downgrade() -> None:
    """The removed copies are redundant: each QCM's file holds its content."""
