import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.annotation import Annotation
from app.models.base import UUIDMixin
from app.models.comment import Comment
from app.models.flag import Flag, FlagStatus
from app.models.material import Material
from app.models.pull_request import PRComment, PullRequest
from app.models.user import User, UserRole

MODERATOR_ROLES = (UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX)

TARGET_TABLE_MAP: dict[str, type[UUIDMixin]] = {
    "material": Material,
    "annotation": Annotation,
    "pull_request": PullRequest,
    "comment": Comment,
    "pr_comment": PRComment,
}


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except ValueError:
        raise BadRequestError(f"Invalid UUID: {value}")


async def _validate_target(db: AsyncSession, target_type: str, target_id: uuid.UUID) -> None:
    model = TARGET_TABLE_MAP.get(target_type)
    if not model:
        raise BadRequestError(f"Invalid target_type: {target_type}")
    target_res = await db.execute(select(model).where(model.id == target_id))
    if not target_res.scalar_one_or_none():
        raise NotFoundError(f"{target_type} not found")


async def create_flag(
    db: AsyncSession,
    reporter_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    reason: str,
    description: str | None = None,
) -> Flag:
    await _validate_target(db, target_type, target_id)

    existing = await db.execute(
        select(Flag).where(
            Flag.reporter_id == reporter_id,
            Flag.target_type == target_type,
            Flag.target_id == target_id,
        )
    )
    if existing.scalar_one_or_none():
        raise BadRequestError("You have already flagged this item")

    flag = Flag(
        reporter_id=reporter_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        description=description,
    )
    db.add(flag)
    await db.flush()

    result = await db.execute(
        select(Flag).options(selectinload(Flag.reporter)).where(Flag.id == flag.id)
    )
    return result.scalar_one()


async def list_flags(
    db: AsyncSession,
    limit: int,
    offset: int,
    status: str | None = None,
    target_type: str | None = None,
) -> tuple[list[Flag], int]:
    base = select(Flag)
    if status:
        base = base.where(Flag.status == FlagStatus(status))
    if target_type:
        base = base.where(Flag.target_type == target_type)

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        base.options(selectinload(Flag.reporter), selectinload(Flag.resolver))
        .order_by(Flag.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().unique().all()), total


async def update_flag(
    db: AsyncSession,
    flag_id: str,
    user: User,
    status: str,
) -> Flag:
    if user.role not in MODERATOR_ROLES:
        raise ForbiddenError("Only moderators can update flags")

    fid = _to_uuid(flag_id)
    result = await db.execute(
        select(Flag)
        .options(selectinload(Flag.reporter), selectinload(Flag.resolver))
        .where(Flag.id == fid)
    )
    flag = result.scalar_one_or_none()
    if not flag:
        raise NotFoundError("Flag not found")

    flag.status = FlagStatus(status)
    flag.resolved_by = user.id
    flag.resolved_at = datetime.now(UTC)
    await db.flush()
    return flag
