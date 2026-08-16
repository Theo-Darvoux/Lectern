from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.collection import CollectionItem
from app.models.directory import Directory, DirectoryFavourite
from app.models.material import Material, MaterialFavourite
from app.schemas.collection import SavedItemOut
from app.services.directory import get_directory_paths


async def _serialize_targets(
    db: AsyncSession,
    material_rows: list[tuple[Material, datetime]],
    directory_rows: list[tuple[Directory, datetime]],
) -> list[SavedItemOut]:
    directory_ids = {
        material.directory_id for material, _ in material_rows if material.directory_id
    }
    directory_ids.update(directory.id for directory, _ in directory_rows)
    paths = await get_directory_paths(db, directory_ids)

    items: list[SavedItemOut] = []
    for material, added_at in material_rows:
        directory_path = paths.get(material.directory_id) if material.directory_id else None
        href = (
            f"/browse/{directory_path}/{material.slug}"
            if directory_path
            else f"/browse/{material.slug}"
        )
        items.append(
            SavedItemOut(
                target_type="material",
                target_id=material.id,
                title=material.title,
                item_type=material.type,
                description=material.description,
                href=href,
                added_at=added_at,
            )
        )

    for directory, added_at in directory_rows:
        path = paths.get(directory.id) or directory.slug
        items.append(
            SavedItemOut(
                target_type="directory",
                target_id=directory.id,
                title=directory.name,
                item_type=(
                    directory.type.value
                    if hasattr(directory.type, "value")
                    else str(directory.type)
                ),
                description=directory.description,
                href=f"/browse/{path}",
                added_at=added_at,
            )
        )

    items.sort(key=lambda item: item.added_at, reverse=True)
    return items


async def get_favourite_items(db: AsyncSession, user_id: uuid.UUID) -> list[SavedItemOut]:
    material_result = await db.execute(
        select(Material, MaterialFavourite.created_at)
        .join(MaterialFavourite, MaterialFavourite.material_id == Material.id)
        .where(
            MaterialFavourite.user_id == user_id,
            Material.deleted_at.is_(None),
        )
        .order_by(MaterialFavourite.created_at.desc())
    )
    material_rows = list(material_result.tuples().all())

    directory_result = await db.execute(
        select(Directory, DirectoryFavourite.created_at)
        .join(DirectoryFavourite, DirectoryFavourite.directory_id == Directory.id)
        .where(
            DirectoryFavourite.user_id == user_id,
            Directory.deleted_at.is_(None),
        )
        .order_by(DirectoryFavourite.created_at.desc())
    )
    directory_rows = list(directory_result.tuples().all())

    return await _serialize_targets(db, material_rows, directory_rows)


async def get_collection_items(db: AsyncSession, collection_id: uuid.UUID) -> list[SavedItemOut]:
    result = await db.execute(
        select(CollectionItem)
        .options(
            selectinload(CollectionItem.material),
            selectinload(CollectionItem.directory),
        )
        .where(CollectionItem.collection_id == collection_id)
        .order_by(CollectionItem.created_at.desc())
    )

    material_rows: list[tuple[Material, datetime]] = []
    directory_rows: list[tuple[Directory, datetime]] = []
    for item in result.scalars().all():
        if item.material is not None and item.material.deleted_at is None:
            material_rows.append((item.material, item.created_at))
        elif item.directory is not None and item.directory.deleted_at is None:
            directory_rows.append((item.directory, item.created_at))

    return await _serialize_targets(db, material_rows, directory_rows)
