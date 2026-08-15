import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from app.core.events.processing import ProcessingFile
from app.core.security.processing_paths import processing_temp_dir
from app.core.storage.facade import download_file, upload_file
from app.models.material import Material, MaterialVersion
from app.services.auth import get_full_auth_config
from app.workers.upload.stages.thumbnail import run_thumbnail_stage

logger = logging.getLogger(__name__)


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
                s3_thumb_key = f"thumbnails/{target_version_id}.webp"

                with open(thumb_path, "rb") as f:
                    thumb_bytes = f.read()
                await upload_file(thumb_bytes, s3_thumb_key, content_type="image/webp")

                async with sessionmaker() as db:
                    await db.execute(
                        update(MaterialVersion)
                        .where(MaterialVersion.id == target_version_id)
                        .values(thumbnail_key=s3_thumb_key, thumbnail_status="ok")
                    )
                    await db.commit()

                logger.info(
                    "Recalculated thumbnail for material %s: %s",
                    material_id,
                    s3_thumb_key,
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
            # Invalidate Redis thumbnail cache
            try:
                from app.core.database.redis import redis_client

                if redis_client is not None:
                    await redis_client.delete(f"thumbnail:v1:{material_id}")
            except Exception:
                pass

    return True
