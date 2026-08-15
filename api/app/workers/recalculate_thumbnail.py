import logging
import uuid
from pathlib import Path
from typing import Any

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


async def _delete_thumbnail_if_unreferenced(sessionmaker: Any, thumbnail_key: str) -> None:
    """Delete a superseded thumbnail only after re-checking authoritative DB liveness."""
    async with sessionmaker() as db:
        await acquire_storage_lifecycle_xact_lock(db, thumbnail_key)
        if await storage_key_is_live(db, thumbnail_key):
            return
        await delete_object(thumbnail_key)
        await db.commit()


async def recalculate_thumbnail(
    ctx: dict[str, Any],
    material_id: str,
    version_id: str | None = None,
) -> bool:
    """Worker task: regenerates the WebP thumbnail for a material version."""
    sessionmaker = ctx.get("db_sessionmaker")
    if sessionmaker is None:
        from app.core.database.database import async_session_factory

        sessionmaker = async_session_factory

    try:
        mid = uuid.UUID(material_id)
    except (ValueError, TypeError):
        logger.error("Invalid material ID for thumbnail recalculation: %s", material_id)
        return False

    vid = uuid.UUID(version_id) if version_id else None

    async with sessionmaker() as db:
        if vid:
            result = await db.execute(select(MaterialVersion).where(MaterialVersion.id == vid))
            version = result.scalar_one_or_none()
        else:
            result = await db.execute(
                select(MaterialVersion)
                .join(Material, Material.id == MaterialVersion.material_id)
                .where(
                    Material.id == mid,
                    MaterialVersion.version_number == Material.current_version,
                )
            )
            version = result.scalar_one_or_none()

        if (
            not version
            or not version.file_key
            or not version.file_name
            or not version.file_mime_type
        ):
            logger.warning(
                "Cannot recalculate thumbnail for material %s: version or file details missing",
                material_id,
            )
            return False

        auth_config = await get_full_auth_config(db)
        file_key = version.file_key
        file_name = version.file_name
        file_mime_type = version.file_mime_type
        target_version_id = version.id
        previous_thumbnail_key = version.thumbnail_key

    generated_thumbnail_key: str | None = None
    published_thumbnail_key: str | None = None
    replaced_thumbnail_key = previous_thumbnail_key

    # Run generation in sandboxed processing directory
    with processing_temp_dir(prefix="thumbnail-recalc-") as tmp_dir:
        try:
            local_path = tmp_dir / Path(file_name).name
            await download_file(file_key, local_path, decompress=True)

            pf = ProcessingFile(local_path, local_path.stat().st_size)
            thumb_path_str = await run_thumbnail_stage(
                pf,
                file_mime_type,
                file_name,
                config=auth_config,
            )

            if thumb_path_str:
                thumb_path = Path(thumb_path_str)

                # Thumbnail responses are edge-cached by object path. Never overwrite
                # a key that may already be cached: publish each regeneration to a
                # fresh immutable key, then atomically switch the DB pointer.
                generated_thumbnail_key = f"thumbnails/{target_version_id}/{uuid.uuid4().hex}.webp"

                with open(thumb_path, "rb") as f:
                    thumb_bytes = f.read()

                # Fence the new key from admin orphan pruning between physical upload
                # and authoritative MaterialVersion publication.
                async with storage_lifecycle_lock(sessionmaker, generated_thumbnail_key):
                    await upload_file(
                        thumb_bytes,
                        generated_thumbnail_key,
                        content_type="image/webp",
                    )

                    # Serialize publication for concurrent regeneration requests. The
                    # last publisher observes the prior key and may safely retire it.
                    async with sessionmaker() as db:
                        result = await db.execute(
                            select(MaterialVersion)
                            .where(MaterialVersion.id == target_version_id)
                            .with_for_update()
                        )
                        current_version = result.scalar_one_or_none()
                        if current_version is None:
                            raise RuntimeError(
                                "Material version disappeared during thumbnail regeneration"
                            )

                        replaced_thumbnail_key = current_version.thumbnail_key
                        current_version.thumbnail_key = generated_thumbnail_key
                        current_version.thumbnail_status = "ok"
                        await db.commit()

                published_thumbnail_key = generated_thumbnail_key

                # Re-check liveness under the same lifecycle fence used by admin
                # pruning before deleting the old object. A recent Upload row may
                # still own the old thumbnail during its grace period.
                if replaced_thumbnail_key and replaced_thumbnail_key != published_thumbnail_key:
                    try:
                        await _delete_thumbnail_if_unreferenced(
                            sessionmaker,
                            replaced_thumbnail_key,
                        )
                    except Exception as cleanup_exc:
                        logger.warning(
                            "Failed to retire superseded thumbnail %s: %s",
                            replaced_thumbnail_key,
                            cleanup_exc,
                        )

                logger.info(
                    "Recalculated thumbnail for material %s: %s",
                    material_id,
                    published_thumbnail_key,
                )
            else:
                async with sessionmaker() as db:
                    await db.execute(
                        update(MaterialVersion)
                        .where(MaterialVersion.id == target_version_id)
                        .values(thumbnail_status="skipped")
                    )
                    await db.commit()
                logger.info(
                    "Thumbnail generation skipped for material %s (unsupported type)",
                    material_id,
                )

        except Exception as exc:
            # If the new object was uploaded but never published, remove it so a
            # failed DB update does not leak storage.
            if generated_thumbnail_key and published_thumbnail_key != generated_thumbnail_key:
                try:
                    await delete_object(generated_thumbnail_key)
                except Exception as cleanup_exc:
                    logger.warning(
                        "Failed to delete unpublished thumbnail %s: %s",
                        generated_thumbnail_key,
                        cleanup_exc,
                    )

            logger.error(
                "Failed to recalculate thumbnail for material %s: %s",
                material_id,
                exc,
            )
            try:
                async with sessionmaker() as db:
                    await db.execute(
                        update(MaterialVersion)
                        .where(MaterialVersion.id == target_version_id)
                        .values(thumbnail_status="failed")
                    )
                    await db.commit()
            except Exception:
                pass
            return False
        finally:
            # Invalidate DB metadata and any signed-URL entries for both sides of
            # the pointer swap. The storage backend owns the exact Redis key format.
            try:
                from app.core.database.redis import redis_client

                if redis_client is not None:
                    await redis_client.delete(f"thumbnail:v1:{material_id}")
                    for thumbnail_key in {
                        previous_thumbnail_key,
                        replaced_thumbnail_key,
                        published_thumbnail_key,
                    }:
                        if thumbnail_key:
                            await bust_presign_cache(thumbnail_key, redis_client)
            except Exception:
                pass

    return True
