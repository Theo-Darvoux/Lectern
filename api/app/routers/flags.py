from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import OnboardedUser, require_moderator
from app.dependencies.pagination import PaginationParams
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.flag import FlagCreateIn, FlagOut, FlagTarget, FlagUpdateIn
from app.services.flag import create_flag, list_flags, resolve_flag_targets, update_flag
from app.services.notification import notify_moderators, notify_user

router = APIRouter(prefix="/api/flags", tags=["flags"])


@router.post("", response_model=FlagOut, status_code=201)
async def add_flag(
    data: FlagCreateIn,
    user: OnboardedUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FlagOut:
    flag = await create_flag(
        db,
        reporter_id=user.id,
        target_type=data.target_type,
        target_id=data.target_id,
        reason=data.reason,
        description=data.description,
    )
    await notify_moderators(
        db,
        "new_flag",
        f"New flag: {data.reason} on {data.target_type}",
        link="/moderator/flags",
    )
    return FlagOut.model_validate(flag)


@router.get("", response_model=PaginatedResponse[FlagOut])
async def get_flags(
    pagination: Annotated[PaginationParams, Depends()],
    _user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str | None, Query()] = None,
    target_type: Annotated[str | None, Query(alias="targetType")] = None,
) -> PaginatedResponse[FlagOut]:
    items, total = await list_flags(
        db,
        limit=pagination.limit,
        offset=pagination.offset,
        status=status,
        target_type=target_type,
    )
    targets = await resolve_flag_targets(db, items)
    out_items: list[FlagOut] = []
    for f in items:
        out = FlagOut.model_validate(f)
        info = targets[f.id]
        out.target = FlagTarget(exists=info.exists, preview=info.preview, link=info.link)
        out_items.append(out)
    return PaginatedResponse(
        items=out_items,
        total=total,
        page=pagination.page,
        pages=(total + pagination.limit - 1) // pagination.limit if total > 0 else 1,
    )


@router.patch("/{flag_id}", response_model=FlagOut)
async def patch_flag(
    flag_id: str,
    data: FlagUpdateIn,
    user: Annotated[User, Depends(require_moderator())],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FlagOut:
    flag = await update_flag(db, flag_id, user, data.status)
    if flag.reporter_id:
        link = (
            f"/browse?material={flag.target_id}"
            if flag.target_type == "material"
            else "/notifications"
        )
        await notify_user(
            db,
            flag.reporter_id,
            "flag_resolved",
            f"Your report was {data.status}",
            link=link,
        )
    return FlagOut.model_validate(flag)
