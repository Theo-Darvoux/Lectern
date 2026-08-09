import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import app.core.database.database as db_core
from app.core.events.meilisearch import meili_admin_client
from app.models.directory import Directory
from app.models.material import Material

logger = logging.getLogger(__name__)

# Precompiled patterns for identifier tokenization (e.g. "CS101" → "CS 101")
_ALPHA_NUM = re.compile(r"([a-zA-Z]+)(\d+)")
_NUM_ALPHA = re.compile(r"(\d+)([a-zA-Z]+)")


def split_identifiers(text: str) -> str:
    if not text:
        return ""
    s = _ALPHA_NUM.sub(r"\1 \2", text)
    s = _NUM_ALPHA.sub(r"\1 \2", s)
    return s


def _build_material_doc(
    material: Material,
    ancestor_path: str,
    browse_path: str,
) -> dict:  # type: ignore[type-arg]
    file_name = None
    file_mime_type = None
    for v in material.versions:
        if v.version_number == material.current_version:
            file_name = v.file_name
            file_mime_type = v.file_mime_type
            break

    extra = f"{split_identifiers(material.title)} {split_identifiers(file_name or '')}"

    return {
        "id": str(material.id),
        "title": material.title,
        "slug": material.slug,
        "description": material.description or "",
        "type": material.type,
        "tags": [t.name for t in material.tags] if material.tags else [],
        "authorName": material.author.display_name if material.author else None,
        "directory_id": str(material.directory_id) if material.directory_id else None,
        "created_at": material.created_at.isoformat() if material.created_at is not None else None,  # type: ignore[redundant-expr]
        "ancestor_path": ancestor_path,
        "extra_searchable": extra,
        "browse_path": browse_path,
        "total_views": material.total_views,
        "views_today": material.views_today,
        "like_count": material.like_count,
        "file_name": file_name,
        "file_mime_type": file_mime_type,
    }


def _build_directory_doc(
    directory: Directory,
    ancestor_path: str,
    browse_path: str,
) -> dict:  # type: ignore[type-arg]
    metadata = directory.metadata_ or {}
    code = metadata.get("code") or ""
    extra = f"{split_identifiers(directory.name)} {split_identifiers(code)}"  # type: ignore[arg-type]

    return {
        "id": str(directory.id),
        "name": directory.name,
        "slug": directory.slug,
        "type": directory.type.value if directory.type else "folder",
        "description": directory.description or "",
        "tags": [t.name for t in directory.tags] if directory.tags else [],
        "code": code,
        "parent_id": str(directory.parent_id) if directory.parent_id else None,
        "created_at": directory.created_at.isoformat()
        if directory.created_at is not None  # type: ignore[redundant-expr]
        else None,
        "ancestor_path": ancestor_path,
        "extra_searchable": extra,
        "browse_path": browse_path,
        "like_count": directory.like_count,
    }


async def index_material(ctx: dict, material_id: uuid.UUID) -> None:  # type: ignore[type-arg]
    """Index or update a single material in Meilisearch."""
    async with db_core.async_session_factory() as db:
        result = await db.execute(
            select(Material)
            .options(
                selectinload(Material.tags),
                selectinload(Material.author),
                selectinload(Material.versions),
            )
            .where(Material.id == material_id, Material.deleted_at.is_(None))
        )
        material = result.scalar_one_or_none()
        if not material:
            logger.warning(f"Material {material_id} not found for indexing.")
            return

        from app.services.directory import get_directory_path

        ancestor_path = ""
        browse_path = "/browse"
        if material.directory_id:
            path_parts = await get_directory_path(db, material.directory_id)
            if path_parts:
                ancestor_path = " ".join(p["name"] for p in path_parts)
                browse_path += "/" + "/".join(p["slug"] for p in path_parts)
        browse_path += f"/{material.slug}"

        doc = _build_material_doc(material, ancestor_path, browse_path)
        await meili_admin_client.index("materials").add_documents([doc])
        logger.info(f"Indexed material {material_id}")


async def index_materials_batch(ctx: dict, material_ids: list[uuid.UUID]) -> None:  # type: ignore[type-arg]
    """Index multiple materials in a single Meilisearch add_documents call."""
    if not material_ids:
        return
    async with db_core.async_session_factory() as db:
        result = await db.execute(
            select(Material)
            .options(
                selectinload(Material.tags),
                selectinload(Material.author),
                selectinload(Material.versions),
            )
            .where(Material.id.in_(material_ids))
        )
        materials = result.scalars().all()
        if not materials:
            return

        from app.services.directory import get_ancestor_map

        dir_ids = {m.directory_id for m in materials if m.directory_id}
        ancestor_map = await get_ancestor_map(db, dir_ids) if dir_ids else {}

        docs = []
        for material in materials:
            ancestor_path = ""
            browse_path = "/browse"
            if material.directory_id:
                paths = ancestor_map.get(material.directory_id)
                if paths:
                    ancestor_path, slug_path = paths
                    browse_path += "/" + slug_path
            browse_path += f"/{material.slug}"
            docs.append(_build_material_doc(material, ancestor_path, browse_path))

        if docs:
            await meili_admin_client.index("materials").add_documents(docs)
            logger.info(f"Batch-indexed {len(docs)} materials")


async def index_directory(ctx: dict, directory_id: uuid.UUID) -> None:  # type: ignore[type-arg]
    """Index or update a single directory in Meilisearch."""
    async with db_core.async_session_factory() as db:
        result = await db.execute(
            select(Directory)
            .options(selectinload(Directory.tags))
            .where(Directory.id == directory_id, Directory.deleted_at.is_(None))
        )
        directory = result.scalar_one_or_none()
        if not directory:
            logger.warning(f"Directory {directory_id} not found for indexing.")
            return

        from app.services.directory import get_directory_path

        ancestor_path = ""
        browse_path = "/browse"
        if directory.parent_id:
            path_parts = await get_directory_path(db, directory.parent_id)
            if path_parts:
                ancestor_path = " ".join(p["name"] for p in path_parts)
                browse_path += "/" + "/".join(p["slug"] for p in path_parts)
        browse_path += f"/{directory.slug}"

        doc = _build_directory_doc(directory, ancestor_path, browse_path)
        await meili_admin_client.index("directories").add_documents([doc])
        logger.info(f"Indexed directory {directory_id}")


async def index_directories_batch(ctx: dict, directory_ids: list[uuid.UUID]) -> None:  # type: ignore[type-arg]
    """Index multiple directories in a single Meilisearch add_documents call."""
    if not directory_ids:
        return
    async with db_core.async_session_factory() as db:
        result = await db.execute(
            select(Directory)
            .options(selectinload(Directory.tags))
            .where(Directory.id.in_(directory_ids))
        )
        directories = result.scalars().all()
        if not directories:
            return

        from app.services.directory import get_ancestor_map

        # For directories, ancestor_path is derived from each directory's PARENT.
        parent_ids = {d.parent_id for d in directories if d.parent_id}
        ancestor_map = await get_ancestor_map(db, parent_ids) if parent_ids else {}

        docs = []
        for directory in directories:
            ancestor_path = ""
            browse_path = "/browse"
            if directory.parent_id:
                paths = ancestor_map.get(directory.parent_id)
                if paths:
                    ancestor_path, slug_path = paths
                    browse_path += "/" + slug_path
            browse_path += f"/{directory.slug}"
            docs.append(_build_directory_doc(directory, ancestor_path, browse_path))

        if docs:
            await meili_admin_client.index("directories").add_documents(docs)
            logger.info(f"Batch-indexed {len(docs)} directories")


async def delete_indexed_item(ctx: dict, index_name: str, item_id: str) -> None:  # type: ignore[type-arg]
    """Delete an item from a specified Meilisearch index."""
    try:
        await meili_admin_client.index(index_name).delete_document(item_id)
        logger.info(f"Deleted {item_id} from {index_name}")
    except Exception as e:
        logger.error(f"Failed to delete {item_id} from {index_name}: {e}")
        raise
