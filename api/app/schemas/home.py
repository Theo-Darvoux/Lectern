from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.directory import DirectoryOut
from app.schemas.material import MaterialDetail
from app.schemas.pull_request import PullRequestOut


class FeaturedItemOut(BaseModel):
    id: uuid.UUID
    material: MaterialDetail | None = None
    directory: DirectoryOut | None = None
    title: str | None
    description: str | None
    start_at: datetime
    end_at: datetime
    priority: int

    model_config = {"from_attributes": True}


class HomeResponse(BaseModel):
    featured: list[FeaturedItemOut]
    popular_today: list[MaterialDetail]
    popular_14d: list[MaterialDetail]
    recent_prs: list[PullRequestOut]
    recent_favourites: list[MaterialDetail]
