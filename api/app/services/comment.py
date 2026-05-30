import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.comment import Comment
from app.models.directory import Directory
from app.models.material import Material
from app.models.user import User, UserRole

logger = logging.getLogger("wikint")


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except ValueError:
        raise BadRequestError(f"Invalid UUID: {value}")


async def validate_target(db: AsyncSession, target_type: str, target_id: str) -> None:
    uid = _to_uuid(target_id)
    if target_type == "directory":
        result = await db.execute(select(Directory).where(Directory.id == uid))
        if not result.scalar_one_or_none():
            raise NotFoundError("Directory not found")
    elif target_type == "material":
        result = await db.execute(select(Material).where(Material.id == uid))
        if not result.scalar_one_or_none():
            raise NotFoundError("Material not found")
    else:
        raise BadRequestError("target_type must be 'directory' or 'material'")


async def get_comments(
    db: AsyncSession,
    target_type: str,
    target_id: str,
    limit: int,
    offset: int,
) -> tuple[list[Comment], int]:
    uid = _to_uuid(target_id)
    base = select(Comment).where(
        Comment.target_type == target_type,
        Comment.target_id == uid,
    )

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        base.options(joinedload(Comment.author))
        .order_by(Comment.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    comments = list(result.scalars().unique().all())
    return comments, total


async def create_comment(
    db: AsyncSession,
    author_id: uuid.UUID,
    target_type: str,
    target_id: str,
    body: str,
) -> Comment:
    await validate_target(db, target_type, target_id)
    uid = _to_uuid(target_id)

    comment = Comment(
        target_type=target_type,
        target_id=uid,
        author_id=author_id,
        body=body,
    )
    db.add(comment)
    await db.flush()

    if target_type == "material":
        from app.services.notification import notify_material_subscribers

        try:
            await notify_material_subscribers(
                db,
                uid,
                author_id,
                "material_comment",
                "New message on a document you follow",
                link=f"/browse?material={uid}",
            )
        except Exception:
            logger.exception("Failed to notify subscribers of new comment")

    result = await db.execute(
        select(Comment).options(joinedload(Comment.author)).where(Comment.id == comment.id)
    )
    return result.scalar_one()


async def update_comment(
    db: AsyncSession,
    comment_id: str,
    user: User,
    body: str,
) -> Comment:
    cid = _to_uuid(comment_id)
    result = await db.execute(
        select(Comment).options(joinedload(Comment.author)).where(Comment.id == cid)
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise NotFoundError("Comment not found")

    if comment.author_id != user.id:
        raise ForbiddenError("Only the author can edit this comment")

    comment.body = body
    comment.updated_at = datetime.now(UTC)
    await db.flush()
    return comment


async def delete_comment(
    db: AsyncSession,
    comment_id: str,
    user: User,
) -> None:
    cid = _to_uuid(comment_id)
    result = await db.execute(select(Comment).where(Comment.id == cid))
    comment = result.scalar_one_or_none()
    if not comment:
        raise NotFoundError("Comment not found")

    is_moderator = user.role in (UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX)
    if comment.author_id != user.id and not is_moderator:
        raise ForbiddenError("Only the author or a moderator can delete this comment")

    await db.delete(comment)
    await db.flush()
