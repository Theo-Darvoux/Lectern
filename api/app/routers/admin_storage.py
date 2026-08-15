import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.core.database.database import get_db
from app.core.database.redis import redis_client
from app.core.security.cas import _STORAGE_USAGE_KEY, hmac_cas_key
from app.core.storage.facade import delete_object, list_objects
from app.core.storage.liveness import (
    acquire_storage_lifecycle_xact_lock,
    storage_key_is_live,
)
from app.models.material import MaterialVersion
from app.models.pull_request import PRStatus, PullRequest
from app.models.upload import Upload
from app.routers.admin import AdminUser

# Only objects under these prefixes may be pruned.  Active materials live under
# cas/ and thumbnails/ exclusively; blocking other prefixes prevents accidental
# deletion of quarantine/ or uploads/ objects that are still in-flight.
_PRUNEABLE_PREFIXES = ("cas/", "thumbnails/")

router = APIRouter(prefix="/api/admin/storage", tags=["Admin Storage"])
logger = logging.getLogger(__name__)


@router.get("/reconcile")
async def reconcile_storage(
    _user: AdminUser,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Identify orphaned S3 objects and missing database files."""
    # 1. Get all file keys from DB
    # We need both main files (via CAS) and thumbnails
    result = await db.execute(select(MaterialVersion.cas_sha256, MaterialVersion.thumbnail_key))
    db_cas_ids = set()
    db_thumbnail_keys = set()
    for cas_sha256, thumbnail_key in result.all():
        if cas_sha256:
            cas_id = hmac_cas_key(cas_sha256).split(":")[-1]
            db_cas_ids.add(cas_id)
        if thumbnail_key:
            db_thumbnail_keys.add(thumbnail_key)

    # Protect CAS objects for uploads that have been processed but not yet approved
    # into a MaterialVersion (clean uploads awaiting PR submission, open PRs).
    # Without this, these objects are wrongly flagged as orphans and pruned.
    upload_result = await db.execute(
        select(Upload.final_key).where(
            Upload.final_key.is_not(None),
            Upload.final_key.like("cas/%"),
            Upload.status.not_in(["failed", "malicious"]),
        )
    )
    for (final_key,) in upload_result.all():
        if final_key:
            db_cas_ids.add(final_key.split("/")[-1])

    # Protect CAS objects referenced by open PRs (not yet approved)
    pr_result = await db.execute(select(PullRequest).where(PullRequest.status == PRStatus.OPEN))
    for pr in pr_result.scalars():
        for op in pr.payload:
            for fk in [op.get("file_key")] + [
                att.get("file_key") if isinstance(att, dict) else None
                for att in (op.get("attachments") or [])  # type: ignore[attr-defined]
            ]:
                if fk and str(fk).startswith("cas/"):
                    db_cas_ids.add(str(fk).split("/")[-1])

    # 2. List all objects in S3
    s3_cas_ids = {}  # cas_id -> size
    s3_thumbnail_keys = {}  # key -> size
    other_objects = []

    total_s3_bytes = 0
    async for obj in list_objects():
        key = obj["Key"]
        size = obj["Size"]
        total_s3_bytes += size

        if key.startswith("cas/"):
            cas_id = key.split("/")[-1]
            s3_cas_ids[cas_id] = size
        elif key.startswith("thumbnails/"):
            s3_thumbnail_keys[key] = size
        else:
            other_objects.append({"key": key, "size": size})

    # 3. Find Orphans (in S3 but not DB)
    orphaned_cas = [
        {"key": f"cas/{cid}", "size": size}
        for cid, size in s3_cas_ids.items()
        if cid not in db_cas_ids
    ]
    orphaned_thumbnails = [
        {"key": key, "size": size}
        for key, size in s3_thumbnail_keys.items()
        if key not in db_thumbnail_keys
    ]

    # 4. Find Missing (in DB but not S3)
    missing_cas = [cid for cid in db_cas_ids if cid not in s3_cas_ids]
    missing_thumbnails = [key for key in db_thumbnail_keys if key not in s3_thumbnail_keys]

    orphan_total_size = sum(o["size"] for o in orphaned_cas) + sum(
        o["size"] for o in orphaned_thumbnails
    )

    return {
        "status": "success",
        "stats": {
            "total_s3_objects": len(s3_cas_ids) + len(s3_thumbnail_keys) + len(other_objects),
            "total_s3_bytes": total_s3_bytes,
            "orphaned_objects_count": len(orphaned_cas) + len(orphaned_thumbnails),
            "orphaned_bytes": orphan_total_size,
            "missing_objects_count": len(missing_cas) + len(missing_thumbnails),
        },
        "orphans": {
            "cas": orphaned_cas,
            "thumbnails": orphaned_thumbnails,
            "others": other_objects,
        },
        "missing": {"cas": missing_cas, "thumbnails": missing_thumbnails},
    }


@router.post("/prune")
async def prune_storage(
    _user: AdminUser,
    keys: list[str],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete specified orphaned objects from S3.

    Only keys under ``cas/`` and ``thumbnails/`` are accepted.  Any other
    prefix is rejected to prevent administrators from accidentally (or
    maliciously) deleting in-flight uploads or other protected objects.
    """
    for key in keys:
        if ".." in key.split("/"):
            raise BadRequestError(
                f"Key '{key}' contains a path traversal sequence and is not allowed."
            )
        if not any(key.startswith(p) for p in _PRUNEABLE_PREFIXES):
            raise BadRequestError(
                f"Key '{key}' is not in a pruneable prefix. "
                f"Only {', '.join(_PRUNEABLE_PREFIXES)} are allowed."
            )

    deleted_count = 0
    skipped_live = 0

    for key in keys:
        try:
            # The caller's reconciliation result is only a candidate list. Re-establish
            # liveness while holding the same PostgreSQL advisory lock used by CAS
            # admission, so a stale reconcile response can never authorize deletion.
            await acquire_storage_lifecycle_xact_lock(db, key)
            if await storage_key_is_live(db, key, redis=redis_client):
                skipped_live += 1
                logger.info("Skipping prune of storage key that became live: %s", key)
                continue
            await delete_object(key)
            deleted_count += 1
        except Exception as e:
            logger.error("Failed to prune object %s: %s", key, e)

    # Trigger a Redis counter re-sync on next check
    await redis_client.delete(_STORAGE_USAGE_KEY)

    return {
        "status": "success",
        "deleted_count": deleted_count,
        "skipped_live_count": skipped_live,
        "message": (
            f"Successfully pruned {deleted_count} objects; "
            f"skipped {skipped_live} that are now live. "
            "Storage counter will be re-synced on next upload."
        ),
    }
