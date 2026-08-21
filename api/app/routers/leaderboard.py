from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.database import get_db
from app.dependencies.auth import CurrentUser
from app.dependencies.pagination import PaginationParams
from app.schemas.leaderboard import LeaderboardPeriod, LeaderboardResponse
from app.services.leaderboard import get_leaderboard

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])


@router.get("", response_model=LeaderboardResponse, include_in_schema=False)
@router.get("/", response_model=LeaderboardResponse)
async def leaderboard(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
    period: Annotated[LeaderboardPeriod, Query()] = "month",
    academic_year: Annotated[Literal["1A", "2A", "3A+"] | None, Query()] = None,
) -> LeaderboardResponse:
    return await get_leaderboard(
        db,
        current_user_id=user.id,
        period=period,
        academic_year=academic_year,
        page=pagination.page,
        limit=pagination.limit,
    )
