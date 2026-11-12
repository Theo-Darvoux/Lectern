import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import NotFoundError
from app.core.database.database import get_db
from app.dependencies.auth import CurrentUser
from app.models.user import User, UserRole
from app.schemas.directory import (
    BrowseDirectory,
    BrowseListing,
    BrowseMaterial,
    BrowseResponse,
    DirectoryBreadcrumb,
    DirectoryChildren,
    DirectoryOut,
    TreeChildren,
)
from app.schemas.material import MaterialDetail, MaterialDetailResponse, PublicMaterialDetail
from app.services.directory import (
    get_directory_by_id,
    get_directory_children,
    get_directory_path,
    get_root_directories,
    get_tree_children,
    resolve_browse_path,
)

router = APIRouter(prefix="/api", tags=["browse"])


def _serialize_browse_material(material: object, user: User) -> MaterialDetailResponse:
    # Guest identities are public read surfaces. Keep storage,
    # moderation, scan, and optimistic-lock metadata on authenticated member views only.
    schema = MaterialDetail if user.role != UserRole.GUEST else PublicMaterialDetail
    return schema.model_validate(material)


def _listing(
    directory: dict[str, Any] | None,
    result: dict[str, Any],
    user: User,
    breadcrumbs: list[dict[str, Any]],
) -> BrowseListing:
    return BrowseListing(
        directory=BrowseDirectory.model_validate(directory) if directory else None,
        directories=[BrowseDirectory.model_validate(d) for d in result.get("directories", [])],
        materials=[_serialize_browse_material(m, user) for m in result.get("materials", [])],
        breadcrumbs=[DirectoryBreadcrumb(**p) for p in breadcrumbs],
    )


@router.get("/browse", response_model=BrowseResponse)
async def browse_root(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> BrowseListing:
    result = await get_root_directories(db, current_user_id=user.id)
    return _listing(None, result, user, [])


@router.get("/browse/{path:path}", response_model=BrowseResponse)
async def browse_path(
    path: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> BrowseListing | BrowseMaterial:
    result = await resolve_browse_path(db, path, current_user_id=user.id)
    # resolve_browse_path walks the directory chain, so it hands the breadcrumbs back.
    breadcrumbs: list[dict[str, Any]] = result.pop("_breadcrumbs", None) or []

    if result["type"] == "material":
        return BrowseMaterial(
            material=_serialize_browse_material(result["material"], user),
            breadcrumbs=[DirectoryBreadcrumb(**p) for p in breadcrumbs],
        )
    return _listing(result.get("directory"), result, user, breadcrumbs)


@router.get("/directories/{directory_id}")
async def get_directory(
    directory_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> DirectoryOut:
    directory = await get_directory_by_id(db, directory_id)
    return DirectoryOut.model_validate(directory)


@router.get("/directories/{directory_id}/children", response_model=DirectoryChildren)
async def get_children(
    directory_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> DirectoryChildren:
    children = await get_directory_children(db, directory_id, current_user_id=user.id)
    return DirectoryChildren(
        directories=[BrowseDirectory.model_validate(d) for d in children["directories"]],
        materials=[_serialize_browse_material(m, user) for m in children["materials"]],
    )


@router.get("/directories/{directory_id}/tree", response_model=TreeChildren)
async def get_tree(
    directory_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> dict[str, Any]:
    """Light children listing for the tree sidebar. ``root`` lists the top level."""
    if directory_id == "root":
        return await get_tree_children(db, None)
    try:
        parsed = uuid.UUID(directory_id)
    except ValueError:
        raise NotFoundError("Directory not found") from None
    return await get_tree_children(db, parsed)


@router.get("/directories/{directory_id}/path")
async def get_path(
    directory_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: CurrentUser,
) -> list[DirectoryBreadcrumb]:
    full_path = await get_directory_path(db, directory_id)
    return [DirectoryBreadcrumb(**p) for p in full_path]
