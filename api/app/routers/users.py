import typing
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import NotFoundError
from app.core.database.database import get_db
from app.core.storage.facade import generate_presigned_get
from app.dependencies.auth import CurrentUser, ReadUser
from app.dependencies.pagination import PaginationParams
from app.models.annotation import Annotation
from app.models.material import Material, MaterialFavourite, MaterialVersion
from app.models.user import UserRole
from app.schemas.collection import SavedLibraryOut
from app.schemas.common import PaginatedResponse
from app.schemas.material import MaterialDetailResponse, project_material_detail
from app.schemas.user import (
    OnboardIn,
    PublicAnnotationContribution,
    PublicMaterialContribution,
    PublicPRContribution,
    PublicUserBrief,
    PublicUserOut,
    PublicUserProfileOut,
    TutorialCompleteIn,
    UserOut,
    UserProfileOut,
    UserUpdateIn,
)
from app.services.avatar import is_owned_avatar_storage_key, is_trusted_external_avatar_url
from app.services.directory import get_directory_paths
from app.services.material import get_liked_favourited_sets, material_orm_to_dict
from app.services.saved import get_favourite_items
from app.services.user import (
    export_user_data,
    get_recently_viewed,
    get_user_by_id,
    get_user_contributions,
    get_user_stats,
    hard_delete_user,
    mark_tutorial_complete,
    onboard_user,
    reset_tutorials,
    update_user_profile,
)

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/me/onboard", response_model=UserOut)
async def onboard(
    data: OnboardIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserOut:
    updated = await onboard_user(db, user, data.display_name, data.academic_year, data.gdpr_consent)
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
) -> UserOut:
    updated = await update_user_profile(
        db,
        user,
        **data.model_dump(exclude_unset=True),
    )
    return UserOut.model_validate(updated)


@router.post("/me/tutorials/complete", response_model=UserOut)
async def complete_tutorial(
    data: TutorialCompleteIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserOut:
    updated = await mark_tutorial_complete(db, user, data.tutorial_id)
    await db.commit()
    return UserOut.model_validate(updated)


@router.delete("/me/tutorials", response_model=UserOut)
async def reset_my_tutorials(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserOut:
    updated = await reset_tutorials(db, user)
    await db.commit()
    return UserOut.model_validate(updated)


@router.get("/me/recently-viewed", response_model=list[MaterialDetailResponse])
async def recently_viewed(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MaterialDetailResponse]:
    materials = await get_recently_viewed(db, str(user.id))

    dir_ids = {m["directory_id"] for m in materials}
    paths = await get_directory_paths(db, dir_ids)

    public = user.role == UserRole.GUEST
    return [
        project_material_detail(
            {**m, "directory_path": paths.get(m["directory_id"])},
            public=public,
        )
        for m in materials
    ]


@router.get("/me/saved", response_model=SavedLibraryOut)
async def get_my_saved_library(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SavedLibraryOut:
    items = await get_favourite_items(db, user.id)
    return SavedLibraryOut(items=items)


@router.get("/me/favourites", response_model=list[MaterialDetailResponse])
async def get_my_favourites(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MaterialDetailResponse]:
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
    rows = (await db.execute(stmt)).all()
    liked_ids, favourited_ids = await get_liked_favourited_sets(
        db, user.id, [material.id for material, _ in rows]
    )

    materials_out = []
    for material, version in rows:
        mat_dict = material_orm_to_dict(
            material,
            current_user_id=user.id,
            current_version=version,
            is_liked=material.id in liked_ids,
            is_favourited=material.id in favourited_ids,
        )
        materials_out.append(mat_dict)

    dir_ids = {m["directory_id"] for m in materials_out if m.get("directory_id") is not None}
    paths = await get_directory_paths(db, dir_ids)

    public = user.role == UserRole.GUEST
    return [
        project_material_detail(
            {**m, "directory_path": paths.get(m["directory_id"])},
            public=public,
        )
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


@router.get("/{user_id}", response_model=PublicUserProfileOut)
async def get_user_profile(
    user_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> PublicUserProfileOut:
    target = await get_user_by_id(db, user_id)
    if not target:
        raise NotFoundError("User not found")
    stats = await get_user_stats(db, user_id)
    user_data = PublicUserOut.model_validate(target).model_dump()
    return PublicUserProfileOut.model_validate({**user_data, **stats})


@router.get("/{user_id}/avatar")
async def get_user_avatar(
    user_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: ReadUser,
) -> RedirectResponse:
    target = await get_user_by_id(db, user_id)
    if not target or not target.avatar_url:
        raise NotFoundError("Avatar not found")

    if is_owned_avatar_storage_key(target.avatar_url, target.id):
        url = await generate_presigned_get(target.avatar_url)
        return RedirectResponse(url)

    if is_trusted_external_avatar_url(target.avatar_url):
        return RedirectResponse(target.avatar_url)

    # Fail closed for legacy or corrupted values. In particular, application
    # storage namespaces (cas/, materials/, quarantine/) must never be presigned
    # through the public avatar endpoint.
    raise NotFoundError("Avatar reference is invalid")


@router.get(
    "/{user_id}/contributions",
    response_model=PaginatedResponse[
        PublicPRContribution | PublicMaterialContribution | PublicAnnotationContribution
    ],
)
async def get_contributions(
    user_id: str,
    pagination: Annotated[PaginationParams, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
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
        current_user_id=user.id,
    )

    directory_paths = {}
    if type == "materials":
        materials_list = cast(list[dict[str, typing.Any]], items)
        dir_ids = {m["directory_id"] for m in materials_list if m.get("directory_id") is not None}
        directory_paths = await get_directory_paths(db, dir_ids)
    elif type == "annotations":
        annotations_list = cast(list[Annotation], items)
        dir_ids = {
            ann.material.directory_id
            for ann in annotations_list
            if ann.material and ann.material.directory_id is not None
        }
        directory_paths = await get_directory_paths(db, dir_ids)

    serialized_items: list[
        PublicPRContribution | PublicMaterialContribution | PublicAnnotationContribution
    ] = []
    for item in items:
        if type == "prs":
            serialized_items.append(PublicPRContribution.model_validate(item))
        elif type == "materials":
            m_item = cast(dict[str, typing.Any], item)
            serialized_items.append(
                PublicMaterialContribution.model_validate(
                    {**m_item, "directory_path": directory_paths.get(m_item["directory_id"])}
                )
            )
        elif type == "annotations":
            ann = cast(Annotation, item)
            mat = ann.material
            dir_path = directory_paths.get(mat.directory_id) if mat and mat.directory_id else None
            serialized_items.append(
                PublicAnnotationContribution(
                    id=ann.id,
                    material_id=ann.material_id,
                    material_title=mat.title if mat else None,
                    material_slug=mat.slug if mat else None,
                    directory_path=dir_path,
                    body=ann.body,
                    author=PublicUserBrief.model_validate(ann.author) if ann.author else None,
                    created_at=ann.created_at,
                    updated_at=ann.updated_at,
                )
            )

    return PaginatedResponse(
        items=serialized_items,
        total=total,
        page=pagination.page,
        pages=(total + pagination.limit - 1) // pagination.limit if total > 0 else 1,
    )
