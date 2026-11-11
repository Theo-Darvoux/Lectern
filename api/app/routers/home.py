from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database.database import get_db
from app.core.database.redis import RedisClient, RedisLockTimeoutError, get_redis, redis_lock
from app.dependencies.auth import CurrentUser
from app.models.directory import Directory
from app.models.featured import FeaturedItem
from app.models.material import Material, MaterialFavourite, MaterialVersion
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import UserRole
from app.models.view_history import ViewHistory
from app.schemas.directory import DirectoryOut
from app.schemas.home import FeaturedItemOut, HomeResponse, HomeStats, PublicHomeResponse
from app.schemas.material import (
    MaterialDetail,
    MaterialDetailResponse,
    project_material_detail,
)
from app.schemas.pull_request import PullRequestOut
from app.services.directory import get_directory_paths, get_preview_material_ids
from app.services.material import get_liked_favourited_sets, material_orm_to_dict
from app.services.pr_visibility import project_pr_for_viewer

router = APIRouter(prefix="/api/home", tags=["home"])


async def _build_material_details(
    db: AsyncSession,
    rows: Any,
    current_user_id: uuid.UUID | None = None,
) -> list[MaterialDetail]:
    """Convert (Material, MaterialVersion?) row pairs into validated MaterialDetail objects.

    Accepts the raw ``result.all()`` return value from SQLAlchemy so that callers
    do not need to cast the opaque ``Sequence[Row[...]]`` type.
    Fetches directory paths in a single batch query to avoid N+1 lookups.
    """
    if not rows:
        return []

    liked_ids, favourited_ids = await get_liked_favourited_sets(
        db, current_user_id, [material.id for material, _ in rows]
    )

    mat_dicts: list[dict[str, Any]] = []
    for material, version in rows:
        mat_dict: dict[str, Any] = material_orm_to_dict(
            material,
            current_user_id=current_user_id,
            current_version=version,
            is_liked=material.id in liked_ids,
            is_favourited=material.id in favourited_ids,
        )
        mat_dicts.append(mat_dict)

    dir_ids = {m["directory_id"] for m in mat_dicts if m.get("directory_id")}
    paths = await get_directory_paths(db, dir_ids)

    return [
        MaterialDetail.model_validate({**m, "directory_path": paths.get(m["directory_id"])})
        for m in mat_dicts
    ]


async def _build_featured_out(
    db: AsyncSession,
    featured_rows: Any,
    current_user_id: uuid.UUID | None = None,
) -> list[FeaturedItemOut]:
    """Convert (FeaturedItem, Material?, MaterialVersion?, Directory?) rows into FeaturedItemOut objects.

    Accepts the raw ``result.all()`` return value from SQLAlchemy.
    Fetches directory paths in a single batch query.
    """
    if not featured_rows:
        return []

    staged_materials: list[tuple[FeaturedItem, dict[str, Any]]] = []
    staged_directories: list[tuple[FeaturedItem, Directory]] = []
    mat_dir_ids: set[uuid.UUID] = set()
    boost_dir_ids: set[uuid.UUID] = set()

    liked_ids, favourited_ids = await get_liked_favourited_sets(
        db, current_user_id, [m.id for _, m, _, _ in featured_rows if m]
    )

    for featured, material, version, directory in featured_rows:
        if material:
            mat_dict: dict[str, Any] = material_orm_to_dict(
                material,
                current_user_id=current_user_id,
                current_version=version,
                is_liked=material.id in liked_ids,
                is_favourited=material.id in favourited_ids,
            )
            if material.directory_id:
                mat_dir_ids.add(material.directory_id)
            staged_materials.append((featured, mat_dict))
        elif directory:
            boost_dir_ids.add(directory.id)
            staged_directories.append((featured, directory))

    all_dir_ids = mat_dir_ids | boost_dir_ids
    paths = await get_directory_paths(db, all_dir_ids)
    preview_ids = await get_preview_material_ids(db, list(boost_dir_ids))

    out: list[FeaturedItemOut] = []
    for featured, mat_dict in staged_materials:
        mat_dict["directory_path"] = paths.get(mat_dict["directory_id"])
        out.append(
            FeaturedItemOut(
                id=featured.id,
                material=MaterialDetail.model_validate(mat_dict),
                directory=None,
                title=featured.title,
                description=featured.description,
                start_at=featured.start_at,
                end_at=featured.end_at,
                priority=featured.priority,
            )
        )

    for featured, directory in staged_directories:
        dir_dict = {
            "id": directory.id,
            "parent_id": directory.parent_id,
            "name": directory.name,
            "slug": directory.slug,
            "type": directory.type,
            "description": directory.description,
            "metadata_": directory.metadata_,
            "sort_order": directory.sort_order,
            "tags": directory.tags,
            "full_path": paths.get(directory.id),
            "preview_material_ids": preview_ids.get(directory.id, []),
            "created_at": directory.created_at,
        }
        out.append(
            FeaturedItemOut(
                id=featured.id,
                material=None,
                directory=DirectoryOut.model_validate(dir_dict),
                title=featured.title,
                description=featured.description,
                start_at=featured.start_at,
                end_at=featured.end_at,
                priority=featured.priority,
            )
        )

    row_id_order = {row[0].id: i for i, row in enumerate(featured_rows)}
    out.sort(key=lambda x: row_id_order.get(x.id, 9999))
    return out


# Versioned: a payload shape change must not read entries written by older code.
_HOME_CACHE_KEY = "cache:home_global:v2"
_HOME_CACHE_TTL_SECONDS = 60


class _HomeGlobal(BaseModel):
    """The part of the home page shared by every viewer, cached for a minute.

    Materials are built without a viewer, so like and favourite flags are
    resolved per request. PRs are stored unprojected; anonymity is applied
    per viewer.
    """

    featured: list[FeaturedItemOut]
    popular_today: list[MaterialDetail]
    popular_14d: list[MaterialDetail]
    recent_prs: list[PullRequestOut]
    recently_added: list[MaterialDetail]
    total_materials: int
    total_directories: int
    open_prs: int


def _with_current_version(stmt: Select[Any]) -> Select[Any]:
    return stmt.outerjoin(
        MaterialVersion,
        (Material.id == MaterialVersion.material_id)
        & (Material.current_version == MaterialVersion.version_number),
    )


def _root_materials(order_by: Any, limit: int) -> Select[Any]:
    return (
        _with_current_version(select(Material, MaterialVersion))
        .where(Material.parent_material_id.is_(None), Material.deleted_at.is_(None))
        .order_by(order_by)
        .limit(limit)
    )


async def _build_home_global(db: AsyncSession) -> _HomeGlobal:
    now = datetime.now(UTC)
    featured_stmt = (
        _with_current_version(
            select(FeaturedItem, Material, MaterialVersion, Directory).outerjoin(
                Material, FeaturedItem.material_id == Material.id
            )
        )
        .outerjoin(Directory, FeaturedItem.directory_id == Directory.id)
        .options(selectinload(Directory.tags))
        .where(FeaturedItem.start_at <= now, FeaturedItem.end_at >= now)
        .order_by(FeaturedItem.priority.desc())
    )
    pr_stmt = (
        select(PullRequest)
        .options(selectinload(PullRequest.author))
        .where(PullRequest.status == PRStatus.OPEN)
        .order_by(PullRequest.created_at.desc())
        .limit(5)
    )
    stats = (
        await db.execute(
            select(
                select(func.count())
                .select_from(Material)
                .where(Material.parent_material_id.is_(None), Material.deleted_at.is_(None))
                .scalar_subquery()
                .label("materials"),
                select(func.count())
                .select_from(Directory)
                .where(Directory.deleted_at.is_(None))
                .scalar_subquery()
                .label("directories"),
                select(func.count())
                .select_from(PullRequest)
                .where(PullRequest.status == PRStatus.OPEN)
                .scalar_subquery()
                .label("open_prs"),
            )
        )
    ).one()

    return _HomeGlobal(
        featured=await _build_featured_out(db, (await db.execute(featured_stmt)).all()),
        popular_today=await _build_material_details(
            db, (await db.execute(_root_materials(Material.views_today.desc(), 8))).all()
        ),
        popular_14d=await _build_material_details(
            db, (await db.execute(_root_materials(Material.views_14d.desc(), 8))).all()
        ),
        recent_prs=[PullRequestOut.model_validate(pr) for pr in (await db.scalars(pr_stmt)).all()],
        recently_added=await _build_material_details(
            db, (await db.execute(_root_materials(Material.created_at.desc(), 8))).all()
        ),
        total_materials=stats.materials or 0,
        total_directories=stats.directories or 0,
        open_prs=stats.open_prs or 0,
    )


async def _home_global(db: AsyncSession, redis: RedisClient) -> _HomeGlobal:
    """Serve the shared payload from cache, letting one request rebuild it at a time."""
    cached = await redis.get(_HOME_CACHE_KEY)
    if cached:
        return _HomeGlobal.model_validate_json(cached)
    try:
        async with redis_lock(redis, "home_cache_build", timeout=5.0):
            cached = await redis.get(_HOME_CACHE_KEY)
            if cached:
                return _HomeGlobal.model_validate_json(cached)
            home = await _build_home_global(db)
            await redis.setex(_HOME_CACHE_KEY, _HOME_CACHE_TTL_SECONDS, home.model_dump_json())
            return home
    except RedisLockTimeoutError:
        # Another request is slow to rebuild it. Serve this one fresh instead of failing.
        return await _build_home_global(db)


@router.get("", response_model=HomeResponse | PublicHomeResponse, include_in_schema=False)
@router.get("/", response_model=HomeResponse | PublicHomeResponse)
async def get_home(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[RedisClient, Depends(get_redis)],
) -> HomeResponse | PublicHomeResponse:
    """Aggregate home-page payload in a single request.

    Returns:
    - **featured**: curated items active right now, ordered by priority DESC
    - **popular_today**: top 8 root materials by views_today DESC
    - **popular_14d**: top 8 root materials by views_14d DESC
    - **recent_prs**: 5 most recently opened open pull requests
    - **recent_favourites**: current user's 6 most recently favourited materials
    - **recently_viewed**: current user's 8 most recently viewed materials
    - **recently_added**: 8 most recently created root materials
    - **stats**: lightweight platform + personal counters
    """
    home = await _home_global(db, redis)

    fav_stmt = (
        _with_current_version(
            select(Material, MaterialVersion).join(
                MaterialFavourite, MaterialFavourite.material_id == Material.id
            )
        )
        .where(MaterialFavourite.user_id == user.id)
        .order_by(MaterialFavourite.created_at.desc())
        .limit(6)
    )
    viewed_stmt = (
        _with_current_version(
            select(Material, MaterialVersion).join(
                ViewHistory, ViewHistory.material_id == Material.id
            )
        )
        .where(ViewHistory.user_id == user.id, Material.deleted_at.is_(None))
        .order_by(ViewHistory.viewed_at.desc())
        .limit(8)
    )
    recent_favourites = await _build_material_details(
        db, (await db.execute(fav_stmt)).all(), user.id
    )
    recently_viewed = await _build_material_details(
        db, (await db.execute(viewed_stmt)).all(), user.id
    )
    my_contributions = await db.scalar(
        select(func.count()).select_from(PullRequest).where(PullRequest.author_id == user.id)
    )

    shared_materials = [
        *home.popular_today,
        *home.popular_14d,
        *home.recently_added,
        *(item.material for item in home.featured if item.material is not None),
    ]
    liked_ids, favourited_ids = await get_liked_favourited_sets(
        db, user.id, [m.id for m in shared_materials]
    )
    for m in shared_materials:
        m.is_liked = m.id in liked_ids
        m.is_favourited = m.id in favourited_ids

    response = HomeResponse(
        featured=home.featured,
        popular_today=home.popular_today,
        popular_14d=home.popular_14d,
        recent_prs=[project_pr_for_viewer(pr, user) for pr in home.recent_prs],
        recent_favourites=recent_favourites,
        recently_viewed=recently_viewed,
        recently_added=home.recently_added,
        stats=HomeStats(
            total_materials=home.total_materials,
            total_directories=home.total_directories,
            open_prs=home.open_prs,
            my_contributions=my_contributions or 0,
        ),
    )
    if user.role == UserRole.GUEST:
        # Guest sessions are publicly mintable. Project the entire aggregate,
        # including recent PR summaries, through the public read boundary.
        return PublicHomeResponse.model_validate(response.model_dump())
    return response


@router.get("/popular", response_model=list[MaterialDetailResponse])
async def get_popular(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    period: Annotated[Literal["today", "14d"], Query(description="Time window")] = "today",
    limit: Annotated[int, Query(ge=1, le=50, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> Sequence[MaterialDetailResponse]:
    """Paginated popular materials for the 'see all' page.

    - **period=today** orders by ``views_today`` DESC
    - **period=14d** orders by ``views_14d`` DESC

    Only root materials (``parent_material_id IS NULL``) are included.
    """
    order_col = Material.views_today if period == "today" else Material.views_14d

    result = await db.execute(_root_materials(order_col.desc(), limit).offset(offset))
    materials = await _build_material_details(db, result.all(), user.id)
    if user.role != UserRole.GUEST:
        return materials
    return [project_material_detail(material.model_dump(), public=True) for material in materials]
