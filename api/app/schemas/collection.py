import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SavedTargetType = Literal["material", "directory"]


class SavedItemOut(BaseModel):
    target_type: SavedTargetType
    target_id: uuid.UUID
    title: str
    item_type: str
    description: str | None = None
    href: str
    added_at: datetime
    metadata: dict[str, object] | None = None
    status: str | None = None
    like_count: int | None = 0
    total_views: int | None = 0
    download_count: int | None = 0
    slug: str | None = None
    current_version_info: dict[str, object] | None = None


class SavedLibraryOut(BaseModel):
    items: list[SavedItemOut]


class CollectionNameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Collection name cannot be empty")
        return normalized


class CollectionItemIn(BaseModel):
    target_type: SavedTargetType
    target_id: uuid.UUID


class CollectionSummaryOut(BaseModel):
    id: uuid.UUID
    name: str
    item_count: int
    created_at: datetime
    updated_at: datetime
    contains_target: bool = False


class CollectionDetailOut(CollectionSummaryOut):
    items: list[SavedItemOut]
