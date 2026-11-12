import logging
import uuid
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select, update

from app.core.events.processing import ProcessingFile
from app.core.security.processing_paths import processing_temp_dir
from app.core.storage.facade import (
    bust_presign_cache,
    delete_object,
    download_file,
    upload_file,
)
from app.core.storage.liveness import (
    acquire_storage_lifecycle_xact_lock,
    storage_key_is_live,
    storage_lifecycle_lock,
)
from app.models.material import Material, MaterialVersion
from app.services.auth import get_full_auth_config
from app.workers.upload.stages.thumbnail import run_thumbnail_stage

logger = logging.getLogger(__name__)

RecalculateResult = Literal["ok", "skipped", "failed"]


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


async def _delete_thumbnail_if_unreferenced(sessionmaker: Any, thumbnail_key: str) -> None:
    """Delete a superseded thumbnail only after re-checking authoritative DB liveness."""
    async with sessionmaker() as db:
        await acquire_storage_lifecycle_xact_lock(db, thumbnail_key)
        if await storage_key_is_live(db, thumbnail_key):
            return
        await delete_object(thumbnail_key)
        await db.commit()


async def _swap_thumbnail_pointer(
    sessionmaker: Any,
    version_id: uuid.UUID,
    thumbnail_key: str | None,
    status: RecalculateResult,
) -> str | None:
    """Point the version at ``thumbnail_key`` and return the key it replaced.

    The row lock serializes concurrent regenerations: the last publisher sees
    the previous key and is the one that retires it.
    """
    async with sessionmaker() as db:
        version = await db.scalar(
            select(MaterialVersion).where(MaterialVersion.id == version_id).with_for_update()
        )
        if version is None:
            raise RuntimeError("Material version disappeared during thumbnail regeneration")
        replaced = version.thumbnail_key
        version.thumbnail_key = thumbnail_key
        version.thumbnail_status = status
        await db.commit()
    return replaced


async def recalculate_thumbnail(
    ctx: dict[str, Any],
    material_id: str,
    version_id: str | None = None,
) -> RecalculateResult:
    """Worker task: regenerate the WebP thumbnail for a material version.

    Returns "ok" when a new thumbnail was published, "skipped" when the file
    has no thumbnail (any previous one is removed), and "failed" otherwise.
    """
    sessionmaker = ctx.get("db_sessionmaker")
    if sessionmaker is None:
        from app.core.database.database import async_session_factory

        sessionmaker = async_session_factory

    mid = _parse_uuid(material_id)
    vid = _parse_uuid(version_id)
    if mid is None or (version_id and vid is None):
        logger.error("Invalid IDs for thumbnail recalculation: %s / %s", material_id, version_id)
        return "failed"

    async with sessionmaker() as db:
        query = select(MaterialVersion).join(Material, Material.id == MaterialVersion.material_id)
        if vid:
            query = query.where(MaterialVersion.id == vid, MaterialVersion.material_id == mid)
        else:
            query = query.where(
                Material.id == mid, MaterialVersion.version_number == Material.current_version
            )
        version = await db.scalar(query)
        if version is None or not version.file_key or not version.file_mime_type:
            logger.warning("Cannot recalculate thumbnail for material %s: no file", material_id)
            return "failed"
        auth_config = await get_full_auth_config(db)
        file_key = version.file_key
        file_name = version.file_name or ""
        file_mime_type = version.file_mime_type
        target_version_id = version.id
        previous_key = version.thumbnail_key

    generated_key: str | None = None
    published = False
    replaced_key: str | None = previous_key
    result: RecalculateResult = "failed"

    with processing_temp_dir(prefix="thumbnail-recalc-") as tmp_dir:
        try:
            # Never derive a local path from the user's filename.
            local_path = tmp_dir / "source"
            await download_file(file_key, local_path, decompress=True)
            pf = ProcessingFile(local_path, local_path.stat().st_size)
            thumb_path = await run_thumbnail_stage(
                pf, file_mime_type, file_name, config=auth_config
            )

            if thumb_path is None:
                replaced_key = await _swap_thumbnail_pointer(
                    sessionmaker, target_version_id, None, "skipped"
                )
                result = "skipped"
            else:
                # Thumbnails are edge-cached by object path: publish every
                # regeneration under a fresh immutable key, then swap the pointer.
                generated_key = f"thumbnails/{target_version_id}/{uuid.uuid4().hex}.webp"
                thumb_bytes = Path(thumb_path).read_bytes()
                # Fence the new key from orphan pruning until the pointer is published.
                async with storage_lifecycle_lock(sessionmaker, generated_key):
                    await upload_file(thumb_bytes, generated_key, content_type="image/webp")
                    replaced_key = await _swap_thumbnail_pointer(
                        sessionmaker, target_version_id, generated_key, "ok"
                    )
                published = True
                result = "ok"
            logger.info("Thumbnail recalculation for material %s: %s", material_id, result)
        except Exception as exc:
            logger.error("Failed to recalculate thumbnail for material %s: %s", material_id, exc)
            if generated_key and not published:
                try:
                    await delete_object(generated_key)
                except Exception as cleanup_exc:
                    logger.warning(
                        "Failed to delete unpublished thumbnail %s: %s", generated_key, cleanup_exc
                    )
            try:
                async with sessionmaker() as db:
                    await db.execute(
                        update(MaterialVersion)
                        .where(MaterialVersion.id == target_version_id)
                        .values(thumbnail_status="failed")
                    )
                    await db.commit()
            except Exception as status_exc:
                logger.warning("Failed to mark thumbnail as failed: %s", status_exc)
            return "failed"
        finally:
            await _invalidate_caches(
                material_id, {previous_key, replaced_key, generated_key if published else None}
            )

    # A recent Upload row may still own the old object during its grace
    # period; the liveness re-check under the lifecycle fence handles that.
    current_key = generated_key if published else None
    if replaced_key and replaced_key != current_key:
        try:
            await _delete_thumbnail_if_unreferenced(sessionmaker, replaced_key)
        except Exception as cleanup_exc:
            logger.warning("Failed to retire thumbnail %s: %s", replaced_key, cleanup_exc)
    return result


async def _invalidate_caches(material_id: str, thumbnail_keys: set[str | None]) -> None:
    """Drop the cached thumbnail lookup and signed URLs on both sides of the swap."""
    from app.core.database.redis import redis_client

    if redis_client is None:
        return
    try:
        await redis_client.delete(f"thumbnail:v1:{material_id}")
        for key in thumbnail_keys:
            if key:
                await bust_presign_cache(key, redis_client)
    except Exception as exc:
        logger.warning("Failed to invalidate thumbnail caches for %s: %s", material_id, exc)
