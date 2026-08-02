import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.core.database.database import get_db
from app.dependencies.auth import get_optional_user
from app.dependencies.rate_limit import rate_limit_search
from app.models.user import User
from app.schemas.pull_request import ALLOWED_MATERIAL_TYPES
from app.services.search import perform_search

router = APIRouter(prefix="/api/search", tags=["search"])

_ALLOWED_TYPE_VALUES = ALLOWED_MATERIAL_TYPES | {"directory"}


@router.get("", dependencies=[Depends(rate_limit_search)])
async def search(
    query: str = Query(..., min_length=1, max_length=200),
    page: int = Query(1, ge=1, le=50),
    limit: int = Query(10, ge=1, le=50),
    directory_id: uuid.UUID | None = Query(None),
    type: str | None = Query(None, max_length=50),
    db: Annotated[AsyncSession, Depends(get_db)] = None,  # type: ignore[assignment]
    user: Annotated[User | None, Depends(get_optional_user)] = None,
) -> dict[str, Any]:
    if type is not None and type not in _ALLOWED_TYPE_VALUES:
        raise BadRequestError(
            f"Invalid type filter. Allowed: {', '.join(sorted(_ALLOWED_TYPE_VALUES))}"
        )

    return await perform_search(
        db,
        query,
        page=page,
        limit=limit,
        current_user_id=user.id if user else None,
        directory_id=directory_id,
        type_filter=type,
    )
