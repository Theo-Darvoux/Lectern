import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.database.database import get_db
from app.dependencies.auth import CurrentUser
from app.models.collection import Collection, CollectionItem
from app.models.directory import Directory
from app.models.material import Material
from app.schemas.collection import (
    CollectionDetailOut,
    CollectionItemIn,
    CollectionNameIn,
    CollectionSummaryOut,
)
from app.services.saved import get_collection_items

router = APIRouter(prefix="/api/collections", tags=["collections"])
TargetType = Literal["material", "directory"]


def _name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


async def _get_owned_collection(
    db: AsyncSession, user_id: uuid.UUID, collection_id: uuid.UUID
) -> Collection:
    collection = await db.scalar(
        select(Collection).where(
            Collection.id == collection_id,
            Collection.user_id == user_id,
        )
    )
    if collection is None:
        # Deliberately return 404 rather than leaking whether another user's
        # collection exists.
        raise NotFoundError("Collection not found")
    return collection


async def _ensure_unique_name(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(Collection.id).where(
        Collection.user_id == user_id,
        Collection.name_key == _name_key(name),
    )
    if exclude_id is not None:
        stmt = stmt.where(Collection.id != exclude_id)
    if await db.scalar(stmt) is not None:
        raise ConflictError("A collection with this name already exists")


async def _ensure_target_exists(
    db: AsyncSession, target_type: TargetType, target_id: uuid.UUID
) -> None:
    if target_type == "material":
        exists = await db.scalar(
            select(Material.id).where(Material.id == target_id, Material.deleted_at.is_(None))
        )
    else:
        exists = await db.scalar(
            select(Directory.id).where(Directory.id == target_id, Directory.deleted_at.is_(None))
        )
    if exists is None:
        raise NotFoundError(f"{target_type.capitalize()} not found")


def _target_filter(target_type: TargetType, target_id: uuid.UUID):
    if target_type == "material":
        return CollectionItem.material_id == target_id
    return CollectionItem.directory_id == target_id


@router.get("", response_model=list[CollectionSummaryOut])
async def list_collections(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    target_type: Annotated[TargetType | None, Query()] = None,
    target_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[CollectionSummaryOut]:
    if (target_type is None) != (target_id is None):
        raise BadRequestError("target_type and target_id must be provided together")

    visible_item_count = func.count(CollectionItem.id).filter(
        (CollectionItem.material_id.is_not(None) & Material.deleted_at.is_(None))
        | (CollectionItem.directory_id.is_not(None) & Directory.deleted_at.is_(None))
    )
    rows = (
        await db.execute(
            select(Collection, visible_item_count)
            .outerjoin(CollectionItem, CollectionItem.collection_id == Collection.id)
            .outerjoin(Material, Material.id == CollectionItem.material_id)
            .outerjoin(Directory, Directory.id == CollectionItem.directory_id)
            .where(Collection.user_id == user.id)
            .group_by(Collection.id)
            .order_by(func.lower(Collection.name), Collection.id)
        )
    ).all()

    containing: set[uuid.UUID] = set()
    if target_type is not None and target_id is not None:
        containing = set(
            (
                await db.scalars(
                    select(CollectionItem.collection_id)
                    .join(Collection, Collection.id == CollectionItem.collection_id)
                    .where(
                        Collection.user_id == user.id,
                        _target_filter(target_type, target_id),
                    )
                )
            ).all()
        )

    return [
        CollectionSummaryOut(
            id=collection.id,
            name=collection.name,
            item_count=item_count,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
            contains_target=collection.id in containing,
        )
        for collection, item_count in rows
    ]


@router.post("", response_model=CollectionSummaryOut, status_code=status.HTTP_201_CREATED)
async def create_collection(
    data: CollectionNameIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CollectionSummaryOut:
    await _ensure_unique_name(db, user.id, data.name)
    collection = Collection(
        user_id=user.id,
        name=data.name,
        name_key=_name_key(data.name),
    )
    db.add(collection)
    await db.commit()
    await db.refresh(collection)
    return CollectionSummaryOut(
        id=collection.id,
        name=collection.name,
        item_count=0,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )


@router.get("/{collection_id}", response_model=CollectionDetailOut)
async def get_collection(
    collection_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CollectionDetailOut:
    collection = await _get_owned_collection(db, user.id, collection_id)
    items = await get_collection_items(db, collection.id)
    return CollectionDetailOut(
        id=collection.id,
        name=collection.name,
        item_count=len(items),
        created_at=collection.created_at,
        updated_at=collection.updated_at,
        items=items,
    )


@router.patch("/{collection_id}", response_model=CollectionSummaryOut)
async def rename_collection(
    collection_id: uuid.UUID,
    data: CollectionNameIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CollectionSummaryOut:
    collection = await _get_owned_collection(db, user.id, collection_id)
    await _ensure_unique_name(db, user.id, data.name, exclude_id=collection.id)
    collection.name = data.name
    collection.name_key = _name_key(data.name)
    await db.commit()
    await db.refresh(collection)
    item_count = len(await get_collection_items(db, collection.id))
    return CollectionSummaryOut(
        id=collection.id,
        name=collection.name,
        item_count=item_count,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    collection = await _get_owned_collection(db, user.id, collection_id)
    await db.delete(collection)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{collection_id}/items", status_code=status.HTTP_204_NO_CONTENT)
async def add_collection_item(
    collection_id: uuid.UUID,
    data: CollectionItemIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await _get_owned_collection(db, user.id, collection_id)
    await _ensure_target_exists(db, data.target_type, data.target_id)
    existing = await db.scalar(
        select(CollectionItem.id).where(
            CollectionItem.collection_id == collection_id,
            _target_filter(data.target_type, data.target_id),
        )
    )
    if existing is None:
        db.add(
            CollectionItem(
                collection_id=collection_id,
                material_id=data.target_id if data.target_type == "material" else None,
                directory_id=data.target_id if data.target_type == "directory" else None,
            )
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{collection_id}/items/{target_type}/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_collection_item(
    collection_id: uuid.UUID,
    target_type: TargetType,
    target_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await _get_owned_collection(db, user.id, collection_id)
    item = await db.scalar(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection_id,
            _target_filter(target_type, target_id),
        )
    )
    if item is not None:
        await db.delete(item)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
