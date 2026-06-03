import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenError, NotFoundError
from app.dependencies.auth import get_current_user
from app.models.pull_request import PRComment
from app.models.user import User, UserRole
from app.schemas.pull_request import PRCommentCreate, PRCommentOut

router = APIRouter(prefix="/api/pr-comments", tags=["pr-comments"])


@router.patch("/{id}", response_model=PRCommentOut)
async def update_pr_comment(
    id: uuid.UUID,
    data: PRCommentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PRCommentOut:
    comment = await db.scalar(select(PRComment).where(PRComment.id == id))
    if not comment:
        raise NotFoundError("PR comment not found")

    if comment.author_id != current_user.id:
        raise ForbiddenError("Only the author can edit this comment")

    comment.body = data.body

    await db.commit()
    await db.refresh(comment, ["author", "updated_at"])
    return PRCommentOut.model_validate(comment)


@router.delete("/{id}")
async def delete_pr_comment(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    comment = await db.scalar(select(PRComment).where(PRComment.id == id))
    if not comment:
        raise NotFoundError("PR comment not found")

    if comment.author_id != current_user.id and current_user.role not in [
        UserRole.MODERATOR,
        UserRole.BUREAU,
        UserRole.VIEUX,
    ]:
        raise ForbiddenError("Not authorized to delete this comment")

    await db.delete(comment)
    return {"status": "ok"}
