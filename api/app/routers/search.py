import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.core.database.database import get_db
from app.core.events.meilisearch import SEARCH_MAX_TOTAL_HITS
from app.dependencies.auth import CurrentUser
from app.dependencies.rate_limit import rate_limit_search
from app.models.content_status import ContentStatus
from app.schemas.pull_request import ALLOWED_DIRECTORY_TYPES, ALLOWED_MATERIAL_TYPES
from app.services.search import perform_search

router = APIRouter(prefix="/api/search", tags=["search"])

_ALLOWED_TYPE_VALUES = ALLOWED_MATERIAL_TYPES | ALLOWED_DIRECTORY_TYPES | {"directory"}
_ALLOWED_STATUS_VALUES = {status.value for status in ContentStatus}
_MAX_SEARCH_PAGE = SEARCH_MAX_TOTAL_HITS * 2


@router.get("", dependencies=[Depends(rate_limit_search)])
async def search(
    query: str = Query(..., min_length=1, max_length=200),
    user: CurrentUser = None,  # type: ignore[assignment]
    # A mixed search can contain SEARCH_MAX_TOTAL_HITS from each index. Keep
    # page reachable even at limit=1; the service still owns the hard hit cap.
    page: int = Query(1, ge=1, le=_MAX_SEARCH_PAGE),
    limit: int = Query(10, ge=1, le=50),
    directory_id: uuid.UUID | None = Query(None),
    type: str | None = Query(None, max_length=50),
    kind: Literal["material", "directory"] | None = Query(None),
    material_type: str | None = Query(None, max_length=50),
    directory_type: str | None = Query(None, max_length=50),
    status: str | None = Query(None, max_length=20),
    recursive: bool = Query(False),
    db: Annotated[AsyncSession, Depends(get_db)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    if type is not None and type not in _ALLOWED_TYPE_VALUES:
        raise BadRequestError(
            f"Invalid type filter. Allowed: {', '.join(sorted(_ALLOWED_TYPE_VALUES))}"
        )
    if material_type is not None and material_type not in ALLOWED_MATERIAL_TYPES:
        raise BadRequestError(
            f"Invalid material type filter. Allowed: {', '.join(sorted(ALLOWED_MATERIAL_TYPES))}"
        )
    if directory_type is not None and directory_type not in ALLOWED_DIRECTORY_TYPES:
        raise BadRequestError(
            f"Invalid directory type filter. Allowed: {', '.join(sorted(ALLOWED_DIRECTORY_TYPES))}"
        )
    if status is not None and status not in _ALLOWED_STATUS_VALUES:
        raise BadRequestError(
            f"Invalid status filter. Allowed: {', '.join(sorted(_ALLOWED_STATUS_VALUES))}"
        )
    if type is not None and (material_type is not None or directory_type is not None):
        raise BadRequestError("type cannot be combined with material_type or directory_type")
    legacy_kind = (
        "directory" if type == "directory" or type in ALLOWED_DIRECTORY_TYPES else "material"
    )
    if type is not None and kind is not None and kind != legacy_kind:
        raise BadRequestError(f"type={type} cannot be combined with kind={kind}")
    if kind == "directory" and material_type is not None:
        raise BadRequestError("material_type cannot be combined with kind=directory")
    if kind == "material" and directory_type is not None:
        raise BadRequestError("directory_type cannot be combined with kind=material")

    return await perform_search(
        db,
        query,
        page=page,
        limit=limit,
        current_user_id=user.id,
        directory_id=directory_id,
        type_filter=type,
        kind_filter=kind,
        material_type_filter=material_type,
        directory_type_filter=directory_type,
        status_filter=status,
        recursive=recursive,
    )
