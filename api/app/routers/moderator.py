from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies.auth import require_moderator
from app.models.directory import Directory
from app.models.featured import FeaturedItem
from app.models.flag import Flag
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User
from app.schemas.featured import FeaturedItemCreate, FeaturedItemUpdate
from app.schemas.home import FeaturedItemOut
from app.schemas.material import MaterialDetail
from app.services.directory import get_directory_paths
from app.services.material import material_orm_to_dict

router = APIRouter(prefix="/api/moderator", tags=["moderator"])


async def _query_featured_rows(
    db: AsyncSession,
    *,
    featured_id: uuid.UUID | None = None,
    order_by_start: bool = False,
) -> Sequence[Any]:
    stmt = (
        select(FeaturedItem, Material, MaterialVersion)
        .join(Material, FeaturedItem.material_id == Material.id)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
    )
    if featured_id is not None:
        stmt = stmt.where(FeaturedItem.id == featured_id)
    if order_by_start:
        stmt = stmt.order_by(FeaturedItem.start_at.desc())

    result = await db.execute(stmt)
    return result.all()


async def _rows_to_featured_out(
    db: AsyncSession,
    rows: Sequence[Any],
) -> list[FeaturedItemOut]:
    if not rows:
        return []

    staged: list[tuple[FeaturedItem, dict[str, Any]]] = []
    dir_ids: set[uuid.UUID] = set()

    for featured, material, version in rows:
        mat_dict: dict[str, Any] = material_orm_to_dict(material)
        if version:
            mat_dict["current_version_info"] = version
        if material.directory_id:
            dir_ids.add(material.directory_id)
        staged.append((featured, mat_dict))

    paths = await get_directory_paths(db, dir_ids)

    out: list[FeaturedItemOut] = []
    for featured, mat_dict in staged:
        mat_dict["directory_path"] = paths.get(mat_dict["directory_id"])
        out.append(
            FeaturedItemOut(
                id=featured.id,
                material=MaterialDetail.model_validate(mat_dict),
                title=featured.title,
                description=featured.description,
                start_at=featured.start_at,
                end_at=featured.end_at,
                priority=featured.priority,
            )
        )
    return out


@router.get("/stats")
async def moderator_stats(
    _user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    user_count = await db.scalar(select(func.count()).select_from(User)) or 0
    material_count = await db.scalar(select(func.count()).select_from(Material)) or 0
    open_pr_count = (
        await db.scalar(
            select(func.count()).select_from(PullRequest).where(PullRequest.status == PRStatus.OPEN)
        )
        or 0
    )
    open_flag_count = (
        await db.scalar(select(func.count()).select_from(Flag).where(Flag.status == "open")) or 0
    )
    return {
        "user_count": user_count,
        "material_count": material_count,
        "open_pr_count": open_pr_count,
        "open_flag_count": open_flag_count,
    }


@router.get("/directories")
async def moderator_list_directories(
    _user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:  # type: ignore[type-arg]
    result = await db.execute(select(Directory).order_by(Directory.sort_order, Directory.name))
    dirs = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "name": d.name,
            "slug": d.slug,
            "type": d.type.value if d.type else None,
            "parent_id": str(d.parent_id) if d.parent_id else None,
            "is_system": d.is_system,
        }
        for d in dirs
    ]


@router.get("/featured", response_model=list[FeaturedItemOut])
async def moderator_list_featured(
    _user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FeaturedItemOut]:
    rows = await _query_featured_rows(db, order_by_start=True)
    return await _rows_to_featured_out(db, rows)


@router.post("/featured", response_model=FeaturedItemOut, status_code=201)
async def moderator_create_featured(
    _user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: Annotated[FeaturedItemCreate, Body()],
) -> FeaturedItemOut:
    if body.end_at <= body.start_at:
        raise BadRequestError("end_at must be after start_at")

    material_exists = await db.scalar(
        select(func.count()).select_from(Material).where(Material.id == body.material_id)
    )
    if not material_exists:
        raise NotFoundError("Material not found")

    featured = FeaturedItem(
        id=uuid.uuid4(),
        material_id=body.material_id,
        title=body.title,
        description=body.description,
        start_at=body.start_at,
        end_at=body.end_at,
        priority=body.priority,
        created_by=_user.id,
    )
    db.add(featured)
    await db.flush()

    rows = await _query_featured_rows(db, featured_id=featured.id)
    if not rows:
        raise NotFoundError("Featured item not found after creation")
    result = await _rows_to_featured_out(db, rows)
    return result[0]


@router.patch("/featured/{featured_id}", response_model=FeaturedItemOut)
async def moderator_update_featured(
    featured_id: uuid.UUID,
    _user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: Annotated[FeaturedItemUpdate, Body()],
) -> FeaturedItemOut:
    featured = await db.scalar(select(FeaturedItem).where(FeaturedItem.id == featured_id))
    if not featured:
        raise NotFoundError("Featured item not found")

    if body.title is not None:
        featured.title = body.title
    if body.description is not None:
        featured.description = body.description
    if body.start_at is not None:
        featured.start_at = body.start_at
    if body.end_at is not None:
        featured.end_at = body.end_at
    if body.priority is not None:
        featured.priority = body.priority

    if featured.end_at <= featured.start_at:
        raise BadRequestError("end_at must be after start_at")

    await db.flush()

    rows = await _query_featured_rows(db, featured_id=featured_id)
    if not rows:
        raise NotFoundError("Featured item not found after update")
    result = await _rows_to_featured_out(db, rows)
    return result[0]


@router.delete("/featured/{featured_id}")
async def moderator_delete_featured(
    featured_id: uuid.UUID,
    _user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    featured = await db.scalar(select(FeaturedItem).where(FeaturedItem.id == featured_id))
    if not featured:
        raise NotFoundError("Featured item not found")

    await db.delete(featured)
    await db.flush()
    return {"status": "ok"}
