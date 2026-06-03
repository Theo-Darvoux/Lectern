import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field, field_validator


class DirectoryOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    slug: str
    type: str
    description: str | None
    metadata: dict[str, object] = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    sort_order: int
    tags: list[str] = []
    full_path: str | None = None
    preview_material_ids: list[str] = []
    created_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def extract_tag_names(cls, v: list[object] | None) -> list[str]:
        if not v:
            return []
        return [tag.name if hasattr(tag, "name") else str(tag) for tag in v]

    model_config = {"from_attributes": True}


class DirectoryWithCounts(DirectoryOut):
    child_directory_count: int = 0
    child_material_count: int = 0
    like_count: int = 0
    is_liked: bool = False
    is_favourited: bool = False


class DirectoryBreadcrumb(BaseModel):
    id: uuid.UUID
    name: str
    slug: str

    model_config = {"from_attributes": True}
