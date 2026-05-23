import typing
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.redis import get_redis
from app.core.storage import generate_presigned_get_url
from app.dependencies.auth import CurrentUser, get_optional_user
from app.dependencies.pagination import PaginationParams
from app.models.user import User
from app.schemas.annotation import AnnotationOut
from app.schemas.common import PaginatedResponse
from app.schemas.material import MaterialDetail
from app.schemas.pull_request import PullRequestOut
from app.schemas.user import OnboardIn, UserOut, UserProfileOut, UserUpdateIn
from app.services.directory import get_directory_paths
from app.services.user import (
    export_user_data,
    get_recently_viewed,
    get_user_by_id,
    get_user_contributions,
    get_user_stats,
    hard_delete_user,
    invalidate_user_cache,
    onboard_user,
    update_user_profile,
)

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/me/onboard", response_model=UserOut)
async def onboard(
    data: OnboardIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> UserOut:
    updated = await onboard_user(db, user, data.display_name, data.academic_year, data.gdpr_consent)
    await invalidate_user_cache(redis, str(user.id))
    return UserOut.model_validate(updated)


@router.get("/me", response_model=UserProfileOut)
async def get_me(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserProfileOut:
    stats = await get_user_stats(db, str(user.id))
    user_data = UserOut.model_validate(user).model_dump()
    return UserProfileOut.model_validate({**user_data, **stats})


@router.patch("/me", response_model=UserOut)
async def patch_me(
    data: UserUpdateIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> UserOut:
    updated = await update_user_profile(
        db,
        user,
        **data.model_dump(exclude_unset=True),
    )
    await invalidate_user_cache(redis, str(user.id))
    return UserOut.model_validate(updated)


@router.get("/me/recently-viewed", response_model=list[MaterialDetail])
async def recently_viewed(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MaterialDetail]:
    materials = await get_recently_viewed(db, str(user.id))

    dir_ids = {m["directory_id"] for m in materials}
    paths = await get_directory_paths(db, dir_ids)

    return [
        MaterialDetail.model_validate({**m, "directory_path": paths.get(m["directory_id"])})
        for m in materials
    ]


@router.get("/me/favourites", response_model=list[MaterialDetail])
async def get_my_favourites(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MaterialDetail]:
    from sqlalchemy import select

    from app.models.material import Material, MaterialFavourite, MaterialVersion
    from app.services.material import material_orm_to_dict

    stmt = (
        select(Material, MaterialVersion)
        .join(MaterialFavourite, MaterialFavourite.material_id == Material.id)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(MaterialFavourite.user_id == user.id)
        .order_by(MaterialFavourite.created_at.desc())
    )
    result = await db.execute(stmt)

    materials_out = []
    for material, version in result.all():
        mat_dict = material_orm_to_dict(material, current_user_id=user.id)
        if version:
            mat_dict["current_version_info"] = version
        materials_out.append(mat_dict)

    dir_ids = {m["directory_id"] for m in materials_out if m.get("directory_id") is not None}
    paths = await get_directory_paths(db, dir_ids)

    return [
        MaterialDetail.model_validate({**m, "directory_path": paths.get(m["directory_id"])})
        for m in materials_out
    ]


@router.get("/me/data-export")
async def data_export(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    data = await export_user_data(db, user)
    return JSONResponse(content=data)


@router.delete("/me", status_code=204)
async def delete_me(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await hard_delete_user(db, user)
    await db.commit()


@router.get("/{user_id}", response_model=UserProfileOut)
async def get_user_profile(
    user_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserProfileOut:
    target = await get_user_by_id(db, user_id)
    if not target:
        raise NotFoundError("User not found")
    stats = await get_user_stats(db, user_id)
    user_data = UserOut.model_validate(target).model_dump()
    return UserProfileOut.model_validate({**user_data, **stats})


@router.get("/{user_id}/avatar")
async def get_user_avatar(
    user_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RedirectResponse:
    target = await get_user_by_id(db, user_id)
    if not target or not target.avatar_url:
        raise NotFoundError("Avatar not found")

    if target.avatar_url.startswith("quarantine/"):
        # Unscanned avatar from a stuck/stale upload.
        # Refuse to serve to avoid 500 error in generate_presigned_get_url.
        raise NotFoundError("Avatar still processing or invalid")

    url = await generate_presigned_get_url(target.avatar_url)
    return RedirectResponse(url)


@router.get("/{user_id}/contributions")
async def get_contributions(
    user_id: str,
    pagination: Annotated[PaginationParams, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)] = None,
    type: Annotated[str, Query()] = "prs",
) -> PaginatedResponse[typing.Any]:
    target = await get_user_by_id(db, user_id)
    if not target:
        raise NotFoundError("User not found")
    items, total = await get_user_contributions(
        db,
        user_id,
        contribution_type=type,
        limit=pagination.limit,
        offset=pagination.offset,
        current_user_id=user.id if user else None,
    )

    directory_paths = {}
    if type == "materials":
        from typing import cast

        materials_list = cast(list[dict[str, typing.Any]], items)
        dir_ids = {m["directory_id"] for m in materials_list if m.get("directory_id") is not None}
        directory_paths = await get_directory_paths(db, dir_ids)

    serialized_items: list[PullRequestOut | MaterialDetail | AnnotationOut] = []
    for item in items:
        if type == "prs":
            serialized_items.append(PullRequestOut.model_validate(item))
        elif type == "materials":
            from typing import cast

            m_item = cast(dict[str, typing.Any], item)
            serialized_items.append(
                MaterialDetail.model_validate(
                    {**m_item, "directory_path": directory_paths.get(m_item["directory_id"])}
                )
            )
        elif type == "annotations":
            serialized_items.append(AnnotationOut.model_validate(item))

    return PaginatedResponse(
        items=serialized_items,
        total=total,
        page=pagination.page,
        pages=(total + pagination.limit - 1) // pagination.limit if total > 0 else 1,
    )
