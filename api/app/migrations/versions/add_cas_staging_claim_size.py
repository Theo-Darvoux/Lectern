"""persist authoritative byte size for CAS staging claims

Revision ID: add_cas_staging_claim_size
Revises: add_directory_root_slug_unique

Existing claims intentionally remain NULL. Their authoritative byte size lives in
object storage and cannot be reconstructed safely from PostgreSQL alone. Runtime
admission therefore fails closed while any unconsumed, unexpired legacy claim is
present; those claims age out under the existing 48-hour lifecycle. New claims
always persist the server-computed serialized byte length.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_cas_staging_claim_size"
down_revision: str | None = "add_directory_root_slug_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cas_staging_claims",
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_cas_staging_claims_size_bytes_nonnegative",
        "cas_staging_claims",
        "size_bytes IS NULL OR size_bytes >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_cas_staging_claims_size_bytes_nonnegative",
        "cas_staging_claims",
        type_="check",
    )
    op.drop_column("cas_staging_claims", "size_bytes")
