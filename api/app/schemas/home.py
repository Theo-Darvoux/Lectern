from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.directory import DirectoryOut
from app.schemas.material import MaterialDetail, PublicMaterialDetail
from app.schemas.pull_request import PullRequestOut
from app.schemas.user import PublicPRContribution


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


class PublicFeaturedItemOut(BaseModel):
    id: uuid.UUID
    material: PublicMaterialDetail | None = None
    directory: DirectoryOut | None = None
    title: str | None
    description: str | None
    start_at: datetime
    end_at: datetime
    priority: int

    model_config = {"from_attributes": True}


class HomeStats(BaseModel):
    total_materials: int
    total_directories: int
    open_prs: int
    my_contributions: int


class HomeResponse(BaseModel):
    featured: list[FeaturedItemOut]
    popular_today: list[MaterialDetail]
    popular_14d: list[MaterialDetail]
    recent_prs: list[PullRequestOut]
    recent_favourites: list[MaterialDetail]
    recently_viewed: list[MaterialDetail]
    recently_added: list[MaterialDetail]
    stats: HomeStats


class PublicHomeResponse(BaseModel):
    featured: list[PublicFeaturedItemOut]
    popular_today: list[PublicMaterialDetail]
    popular_14d: list[PublicMaterialDetail]
    recent_prs: list[PublicPRContribution]
    recent_favourites: list[PublicMaterialDetail]
    recently_viewed: list[PublicMaterialDetail]
    recently_added: list[PublicMaterialDetail]
    stats: HomeStats
