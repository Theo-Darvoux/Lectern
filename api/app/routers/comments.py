import math
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.database import get_db
from app.core.events.limiter import limiter
from app.dependencies.auth import CurrentUser, OnboardedUser
from app.dependencies.pagination import PaginationParams
from app.schemas.comment import CommentCreateIn, CommentOut, CommentUpdateIn
from app.schemas.common import PaginatedResponse
from app.services.comment import (
    create_comment,
    delete_comment,
    get_comments,
    update_comment,
)

router = APIRouter(prefix="/api/comments", tags=["comments"])


@router.get("", response_model=PaginatedResponse[CommentOut])
async def list_comments(
    target_type: Annotated[str, Query(alias="targetType")],
    target_id: Annotated[str, Query(alias="targetId")],
    pagination: Annotated[PaginationParams, Depends()],
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PaginatedResponse[CommentOut]:
    comments, total = await get_comments(
        db, target_type, target_id, pagination.limit, pagination.offset
    )
    return PaginatedResponse[CommentOut](
        items=[CommentOut.model_validate(c) for c in comments],
        total=total,
        page=pagination.page,
        pages=max(1, math.ceil(total / pagination.limit)),
    )


@router.post("", response_model=CommentOut, status_code=201)
@limiter.limit("10/minute")
async def add_comment(
    request: Request,
    data: CommentCreateIn,
    user: OnboardedUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CommentOut:
    comment = await create_comment(db, user.id, data.target_type, data.target_id, data.body)
    return CommentOut.model_validate(comment)


@router.patch("/{comment_id}", response_model=CommentOut)
async def edit_comment(
    comment_id: str,
    data: CommentUpdateIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CommentOut:
    comment = await update_comment(db, comment_id, user, data.body)
    return CommentOut.model_validate(comment)


@router.delete("/{comment_id}", status_code=204)
async def remove_comment(
    comment_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await delete_comment(db, comment_id, user)
