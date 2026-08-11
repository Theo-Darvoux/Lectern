from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _reaction_lock_key(kind: str, user_id: uuid.UUID, target_id: uuid.UUID) -> int:
    """Return a stable signed bigint for a per-user reaction toggle."""
    payload = f"{kind}:{user_id}:{target_id}".encode()
    digest = hashlib.blake2b(
        payload,
        digest_size=8,
        person=b"wikint-like",
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


async def acquire_reaction_toggle_lock(
    db: AsyncSession,
    *,
    kind: str,
    user_id: uuid.UUID,
    target_id: uuid.UUID,
) -> None:
    """Serialize one logical like toggle for the lifetime of the transaction.

    A row lock cannot protect the initial unliked state because no membership row
    exists yet. PostgreSQL advisory locks give the (user, target) pair a durable
    serialization point without forcing all users liking the same item through a
    single target-row lock. SQLite is used only by local/unit tests and already
    serializes writers at the database level.
    """
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {
                "lock_key": _reaction_lock_key(kind, user_id, target_id),
            },
        )
    elif dialect != "sqlite":
        raise RuntimeError(f"Unsupported database dialect for reaction toggles: {dialect}")
