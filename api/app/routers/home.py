from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.redis import get_redis, redis_lock
from app.dependencies.auth import CurrentUser
from app.models.directory import Directory
from app.models.featured import FeaturedItem
from app.models.material import Material, MaterialFavourite, MaterialVersion
from app.models.pull_request import PRStatus, PullRequest
from app.models.view_history import ViewHistory
from app.schemas.directory import DirectoryOut
from app.schemas.home import FeaturedItemOut, HomeResponse, HomeStats
from app.schemas.material import MaterialDetail
from app.schemas.pull_request import PullRequestOut
from app.services.directory import get_directory_paths, get_preview_material_ids
from app.services.material import get_liked_favourited_sets, material_orm_to_dict

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


@router.get("/", response_model=HomeResponse)
async def get_home(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> HomeResponse:
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
    now = datetime.now(UTC)

    # ── popular_today ─────────────────────────────────────────────────────────
    today_stmt = (
        select(Material, MaterialVersion)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(Material.parent_material_id.is_(None))
        .order_by(Material.views_today.desc())
        .limit(8)
    )

    # ── popular_14d ───────────────────────────────────────────────────────────
    week2_stmt = (
        select(Material, MaterialVersion)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(Material.parent_material_id.is_(None))
        .order_by(Material.views_14d.desc())
        .limit(8)
    )

    # ── featured ──────────────────────────────────────────────────────────────
    featured_stmt = (
        select(FeaturedItem, Material, MaterialVersion, Directory)
        .outerjoin(Material, FeaturedItem.material_id == Material.id)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .outerjoin(Directory, FeaturedItem.directory_id == Directory.id)
        .options(selectinload(Directory.tags))
        .where(
            FeaturedItem.start_at <= now,
            FeaturedItem.end_at >= now,
        )
        .order_by(FeaturedItem.priority.desc())
    )

    # ── recent open PRs ───────────────────────────────────────────────────────
    pr_stmt = (
        select(PullRequest)
        .options(selectinload(PullRequest.author))
        .where(PullRequest.status == PRStatus.OPEN)
        .order_by(PullRequest.created_at.desc())
        .limit(5)
    )

    # ── recent favourites ─────────────────────────────────────────────────────
    fav_stmt = (
        select(Material, MaterialVersion)
        .join(MaterialFavourite, MaterialFavourite.material_id == Material.id)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(MaterialFavourite.user_id == user.id)
        .order_by(MaterialFavourite.created_at.desc())
        .limit(6)
    )

    # ── recently viewed ───────────────────────────────────────────────────────
    viewed_stmt = (
        select(Material, MaterialVersion)
        .join(ViewHistory, ViewHistory.material_id == Material.id)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(ViewHistory.user_id == user.id, Material.deleted_at.is_(None))
        .order_by(ViewHistory.viewed_at.desc())
        .limit(8)
    )

    # ── recently added ────────────────────────────────────────────────────────
    added_stmt = (
        select(Material, MaterialVersion)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(Material.parent_material_id.is_(None), Material.deleted_at.is_(None))
        .order_by(Material.created_at.desc())
        .limit(8)
    )

    # ── stats ─────────────────────────────────────────────────────────────────
    stats_query = select(
        select(func.count())
        .select_from(Material)
        .where(Material.parent_material_id.is_(None), Material.deleted_at.is_(None))
        .scalar_subquery()
        .label("m_count"),
        select(func.count())
        .select_from(Directory)
        .where(Directory.deleted_at.is_(None))
        .scalar_subquery()
        .label("d_count"),
        select(func.count())
        .select_from(PullRequest)
        .where(PullRequest.status == PRStatus.OPEN)
        .scalar_subquery()
        .label("pr_count"),
        select(func.count())
        .select_from(PullRequest)
        .where(PullRequest.author_id == user.id)
        .scalar_subquery()
        .label("my_pr_count"),
    )

    # Check cache for global queries
    global_cache = await redis.get("cache:home_global")
    if not global_cache:
        import asyncio

        try:
            async with redis_lock(redis, "home_cache_build", timeout=0.1):
                # Cache miss: execute global queries
                today_rows = (await db.execute(today_stmt)).all()
                week2_rows = (await db.execute(week2_stmt)).all()
                featured_rows = (await db.execute(featured_stmt)).all()
                pr_rows = (await db.execute(pr_stmt)).scalars().all()
                added_rows = (await db.execute(added_stmt)).all()
                stats_row = (await db.execute(stats_query)).one()

                recent_prs = [PullRequestOut.model_validate(pr) for pr in pr_rows]

                # Consolidate all directory paths fetching
                dir_ids = set()
                for rows in (today_rows, week2_rows, added_rows):
                    for material, _ in rows:
                        if material.directory_id:
                            dir_ids.add(material.directory_id)

                boost_dir_ids = set()
                for featured_item, material, version, directory in featured_rows:
                    if material and material.directory_id:
                        dir_ids.add(material.directory_id)
                    elif directory:
                        boost_dir_ids.add(directory.id)
                        dir_ids.add(directory.id)

                paths = await get_directory_paths(db, dir_ids)
                preview_ids = (
                    await get_preview_material_ids(db, list(boost_dir_ids)) if boost_dir_ids else {}
                )

                def build_mat_list(rows: Any) -> list[MaterialDetail]:
                    res = []
                    for material, version in rows:
                        mat_dict = material_orm_to_dict(
                            material, current_user_id=None, current_version=version
                        )
                        mat_dict["directory_path"] = paths.get(mat_dict["directory_id"])
                        res.append(MaterialDetail.model_validate(mat_dict))
                    return res

                popular_today = build_mat_list(today_rows)
                popular_14d = build_mat_list(week2_rows)
                recently_added = build_mat_list(added_rows)

                # Build featured
                featured_out = []
                for featured_item, material, version, directory in featured_rows:
                    if material:
                        mat_dict = material_orm_to_dict(
                            material, current_user_id=None, current_version=version
                        )
                        mat_dict["directory_path"] = paths.get(mat_dict["directory_id"])
                        featured_out.append(
                            FeaturedItemOut(
                                id=featured_item.id,
                                material=MaterialDetail.model_validate(mat_dict),
                                directory=None,
                                title=featured_item.title,
                                description=featured_item.description,
                                start_at=featured_item.start_at,
                                end_at=featured_item.end_at,
                                priority=featured_item.priority,
                            )
                        )
                    elif directory:
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
                        featured_out.append(
                            FeaturedItemOut(
                                id=featured_item.id,
                                material=None,
                                directory=DirectoryOut.model_validate(dir_dict),
                                title=featured_item.title,
                                description=featured_item.description,
                                start_at=featured_item.start_at,
                                end_at=featured_item.end_at,
                                priority=featured_item.priority,
                            )
                        )

                row_id_order = {row[0].id: i for i, row in enumerate(featured_rows)}
                featured_out.sort(key=lambda x: row_id_order.get(x.id, 9999))

                # Save to cache
                cache_payload = {
                    "popular_today": [m.model_dump() for m in popular_today],
                    "popular_14d": [m.model_dump() for m in popular_14d],
                    "recent_prs": [m.model_dump() for m in recent_prs],
                    "recently_added": [m.model_dump() for m in recently_added],
                    "featured": [m.model_dump() for m in featured_out],
                    "stats": {
                        "m_count": stats_row.m_count,
                        "d_count": stats_row.d_count,
                        "pr_count": stats_row.pr_count,
                    },
                }

                # JSON serialize with custom UUID/datetime encoder
                class Encoder(json.JSONEncoder):
                    def default(self, obj):
                        from datetime import datetime
                        from uuid import UUID

                        if isinstance(obj, UUID):
                            return str(obj)
                        if isinstance(obj, datetime):
                            return obj.isoformat()
                        return super().default(obj)

                global_cache_str = json.dumps(cache_payload, cls=Encoder)
                await redis.setex("cache:home_global", 60, global_cache_str)
                global_cache = global_cache_str
        except TimeoutError:
            # Someone else is building the cache. Wait for them.
            for _ in range(30):
                await asyncio.sleep(0.5)
                global_cache = await redis.get("cache:home_global")
                if global_cache:
                    break
            else:
                # Fallback if cache build takes > 15s or fails
                raise RuntimeError("Timeout waiting for home page cache")

    # Execute user-specific queries always
    fav_rows = (await db.execute(fav_stmt)).all()
    viewed_rows = (await db.execute(viewed_stmt)).all()

    if global_cache:
        cached_data = json.loads(global_cache)
        popular_today = [MaterialDetail.model_validate(m) for m in cached_data["popular_today"]]
        popular_14d = [MaterialDetail.model_validate(m) for m in cached_data["popular_14d"]]
        recent_prs = [PullRequestOut.model_validate(m) for m in cached_data["recent_prs"]]
        recently_added = [MaterialDetail.model_validate(m) for m in cached_data["recently_added"]]
        featured_out = [FeaturedItemOut.model_validate(m) for m in cached_data["featured"]]
        global_stats = cached_data["stats"]

        # User-specific stats must be fetched even if cache hits
        my_pr_count = await db.scalar(
            select(func.count()).select_from(PullRequest).where(PullRequest.author_id == user.id)
        )
        stats_row = type(
            "StatsRow",
            (),
            {
                "m_count": global_stats["m_count"],
                "d_count": global_stats["d_count"],
                "pr_count": global_stats["pr_count"],
                "my_pr_count": my_pr_count,
            },
        )()

        user_dir_ids = set()
        for rows in (fav_rows, viewed_rows):
            for material, _ in rows:
                if material.directory_id:
                    user_dir_ids.add(material.directory_id)
        paths = await get_directory_paths(db, user_dir_ids)

    # User specific lists are built using the fetched paths
    def build_user_mat_list(rows: Any) -> list[MaterialDetail]:
        res = []
        for material, version in rows:
            mat_dict = material_orm_to_dict(
                material, current_user_id=user.id, current_version=version
            )
            mat_dict["directory_path"] = paths.get(mat_dict["directory_id"])
            res.append(MaterialDetail.model_validate(mat_dict))
        return res

    recent_favourites = build_user_mat_list(fav_rows)
    recently_viewed = build_user_mat_list(viewed_rows)

    # Resolve per-user like / favourite state across every material on the page.
    # The global lists (featured, popular, recently_added) come from a shared
    # cross-user cache built with current_user_id=None, so their is_liked /
    # is_favourited flags are always False as cached. Patch them per request
    # here with a single batched lookup instead of baking user state into the
    # shared cache.
    all_materials: list[MaterialDetail] = [
        *popular_today,
        *popular_14d,
        *recently_added,
        *recent_favourites,
        *recently_viewed,
        *(item.material for item in featured_out if item.material is not None),
    ]
    liked_ids, favourited_ids = await get_liked_favourited_sets(
        db, user.id, [m.id for m in all_materials]
    )
    for m in all_materials:
        m.is_liked = m.id in liked_ids
        m.is_favourited = m.id in favourited_ids

    stats = HomeStats(
        total_materials=stats_row.m_count or 0,
        total_directories=stats_row.d_count or 0,
        open_prs=stats_row.pr_count or 0,
        my_contributions=stats_row.my_pr_count or 0,
    )

    return HomeResponse(
        featured=featured_out,
        popular_today=popular_today,
        popular_14d=popular_14d,
        recent_prs=recent_prs,
        recent_favourites=recent_favourites,
        recently_viewed=recently_viewed,
        recently_added=recently_added,
        stats=stats,
    )


@router.get("/popular", response_model=list[MaterialDetail])
async def get_popular(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    period: Annotated[Literal["today", "14d"], Query(description="Time window")] = "today",
    limit: Annotated[int, Query(ge=1, le=50, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> list[MaterialDetail]:
    """Paginated popular materials for the 'see all' page.

    - **period=today** orders by ``views_today`` DESC
    - **period=14d** orders by ``views_14d`` DESC

    Only root materials (``parent_material_id IS NULL``) are included.
    """
    order_col = Material.views_today if period == "today" else Material.views_14d

    result = await db.execute(
        select(Material, MaterialVersion)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(Material.parent_material_id.is_(None))
        .order_by(order_col.desc())
        .offset(offset)
        .limit(limit)
    )
    return await _build_material_details(db, result.all(), user.id)
