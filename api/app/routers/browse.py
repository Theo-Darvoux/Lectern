import typing
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_optional_user
from app.models.user import User
from app.schemas.directory import DirectoryBreadcrumb, DirectoryOut
from app.schemas.material import MaterialDetail
from app.services.directory import (
    get_directory_by_id,
    get_directory_children,
    get_directory_path,
    get_root_directories,
    resolve_browse_path,
)

router = APIRouter(prefix="/api", tags=["browse"])


@router.get("/browse")
async def browse_root(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)] = None,
) -> dict[str, typing.Any]:
    result = await get_root_directories(db, current_user_id=user.id if user else None)
    materials = [MaterialDetail.model_validate(m).model_dump() for m in result.get("materials", [])]
    return {
        "type": "directory_listing",
        "directory": None,
        "directories": result.get("directories", []),
        "materials": materials,
    }


@router.get("/browse/{path:path}")
async def browse_path(
    path: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)] = None,
) -> dict[str, typing.Any]:
    result = await resolve_browse_path(db, path, current_user_id=user.id if user else None)

    # resolve_browse_path already computes the directory path for directory
    # listings; reuse it instead of running the recursive path CTE again.
    precomputed = result.pop("_breadcrumbs", None)
    if precomputed is not None:
        breadcrumbs = [DirectoryBreadcrumb(**p).model_dump() for p in precomputed]
    else:
        # Determine which directory to use for breadcrumbs
        directory_id = None
        if result["type"] == "material":
            directory_id = result["material"].get("directory_id")
        elif result["type"] == "directory_listing":
            directory_id = (
                result.get("directory", {}).get("id") if result.get("directory") else None
            )
        elif result["type"] == "attachment_listing":
            # For attachments, breadcrumbs should be relative to the parent material's directory
            directory_id = result.get("parent_material", {}).get("directory_id")

        breadcrumbs = []
        if directory_id:
            path_data = await get_directory_path(db, directory_id)
            breadcrumbs = [DirectoryBreadcrumb(**p).model_dump() for p in path_data]

    return {
        **result,
        "breadcrumbs": breadcrumbs,
    }


@router.get("/directories/{directory_id}")
async def get_directory(
    directory_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DirectoryOut:
    directory = await get_directory_by_id(db, directory_id)
    return DirectoryOut.model_validate(directory)


@router.get("/directories/{directory_id}/children")
async def get_children(
    directory_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)] = None,
) -> dict[str, typing.Any]:
    children = await get_directory_children(
        db, directory_id, current_user_id=user.id if user else None
    )
    materials = [MaterialDetail.model_validate(m).model_dump() for m in children["materials"]]
    return {"directories": children["directories"], "materials": materials}


@router.get("/directories/{directory_id}/path")
async def get_path(
    directory_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[DirectoryBreadcrumb]:
    full_path = await get_directory_path(db, directory_id)
    return [DirectoryBreadcrumb(**p) for p in full_path]
