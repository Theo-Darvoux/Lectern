import uuid
from typing import Literal

from pydantic import BaseModel, field_serializer

from app.services.avatar import is_safe_avatar_reference

LeaderboardPeriod = Literal["month", "semester", "all_time"]


class LeaderboardEntry(BaseModel):
    rank: int
    user_id: uuid.UUID
    display_name: str | None
    avatar_url: str | None
    academic_year: str | None
    approved_contributions: int
    annotations: int
    score: int

    @field_serializer("avatar_url")
    def serialize_avatar_url(self, value: str | None) -> str | None:
        return value if is_safe_avatar_reference(value, self.user_id) else None


class LeaderboardResponse(BaseModel):
    items: list[LeaderboardEntry]
    current_user: LeaderboardEntry | None
    total: int
    page: int
    pages: int
    period: LeaderboardPeriod
    academic_year: str | None
