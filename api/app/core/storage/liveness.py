"""Authoritative storage-key liveness and lifecycle serialization."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.cas_staging_claim import CasStagingClaim
from app.models.material import MaterialVersion
from app.models.pull_request import PRFileClaim
from app.models.upload import Upload

_LIFECYCLE_PREFIXES = ("cas/", "thumbnails/")


def _is_lifecycle_key(file_key: str) -> bool:
    return file_key.startswith(_LIFECYCLE_PREFIXES)


def _storage_lifecycle_lock_key(file_key: str) -> int:
    """Map one managed storage key onto PostgreSQL's signed bigint lock key."""
    digest = hashlib.blake2b(
        file_key.encode(),
        digest_size=8,
        person=b"lectern-store",
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


async def acquire_storage_lifecycle_xact_lock(db: AsyncSession, file_key: str) -> None:
    """Serialize managed-object publication/deletion for the current transaction."""
    if not _is_lifecycle_key(file_key):
        return
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _storage_lifecycle_lock_key(file_key)},
        )
    elif dialect != "sqlite":
        raise RuntimeError(f"Unsupported database dialect for storage lifecycle locking: {dialect}")


@asynccontextmanager
async def storage_lifecycle_lock(
    session_factory: async_sessionmaker[AsyncSession] | None,
    file_key: str,
) -> AsyncGenerator[None, None]:
    """Hold a session-level object lock across storage I/O and DB publication.

    Workers write CAS/thumbnail bytes before publishing the authoritative Upload row.
    A transaction-scoped lock cannot span those independently managed operations, so
    workers use a PostgreSQL session advisory lock. Request/cleanup transactions use
    :func:`acquire_storage_lifecycle_xact_lock`; both lock forms conflict on the same key.

    Hermetic tests sometimes provide mock callables instead of a SQLAlchemy session
    factory. Those cannot provide a real PostgreSQL fence and intentionally no-op here.
    """
    if session_factory is None or not _is_lifecycle_key(file_key):
        yield
        return
    if not (isinstance(session_factory, async_sessionmaker) or inspect.isfunction(session_factory)):
        yield
        return

    async with session_factory() as db:
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            yield
            return
        if dialect != "postgresql":
            raise RuntimeError(
                f"Unsupported database dialect for storage lifecycle locking: {dialect}"
            )

        lock_key = _storage_lifecycle_lock_key(file_key)
        await db.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": lock_key})
        try:
            yield
        finally:
            await db.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key})


async def storage_key_is_live(
    db: AsyncSession,
    file_key: str,
    *,
    redis: Any | None = None,
    now: datetime | None = None,
) -> bool:
    """Re-establish whether a prune candidate has a current authoritative owner.

    This is intentionally conservative. False positives leak storage temporarily;
    false negatives can destroy live data.
    """
    now = now or datetime.now(UTC)
    revert_cutoff = now - timedelta(days=settings.pr_revert_grace_days)
    orphan_cutoff = now - timedelta(hours=48)

    if file_key.startswith("cas/"):
        material_live = await db.scalar(
            select(MaterialVersion.id)
            .where(
                MaterialVersion.file_key == file_key,
                or_(
                    MaterialVersion.deleted_at.is_(None),
                    MaterialVersion.deleted_at >= revert_cutoff,
                ),
            )
            .execution_options(include_deleted=True)
            .limit(1)
        )
        if material_live is not None:
            return True

        upload_live = await db.scalar(
            select(Upload.id)
            .where(
                Upload.final_key == file_key,
                or_(Upload.cas_ref_count > 0, Upload.updated_at >= orphan_cutoff),
            )
            .limit(1)
        )
        if upload_live is not None:
            return True

        claim_live = await db.scalar(
            select(CasStagingClaim.id)
            .where(
                CasStagingClaim.file_key == file_key,
                or_(
                    and_(
                        CasStagingClaim.consumed_at.is_(None),
                        CasStagingClaim.expires_at > now,
                    ),
                    CasStagingClaim.consumed_at.is_not(None),
                ),
            )
            .limit(1)
        )
        if claim_live is not None:
            return True

        pr_claim = await db.scalar(
            select(PRFileClaim.file_key).where(PRFileClaim.file_key == file_key).limit(1)
        )
        if pr_claim is not None:
            return True

        if redis is not None:
            cas_id = file_key.split("/", 1)[1]
            try:
                if await redis.exists(f"upload:cas:{cas_id}"):
                    return True
            except Exception:
                # Redis is not the ownership authority. A failed cache lookup must not
                # turn a known DB-live object into an orphan, but DB-negative cleanup can
                # proceed because admission/deletion is serialized in PostgreSQL.
                pass
        return False

    if file_key.startswith("thumbnails/"):
        material_live = await db.scalar(
            select(MaterialVersion.id)
            .where(
                MaterialVersion.thumbnail_key == file_key,
                or_(
                    MaterialVersion.deleted_at.is_(None),
                    MaterialVersion.deleted_at >= revert_cutoff,
                ),
            )
            .execution_options(include_deleted=True)
            .limit(1)
        )
        if material_live is not None:
            return True

        upload_live = await db.scalar(
            select(Upload.id)
            .where(
                Upload.thumbnail_key == file_key,
                Upload.updated_at >= orphan_cutoff,
            )
            .limit(1)
        )
        return upload_live is not None

    return False
