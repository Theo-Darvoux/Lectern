import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.schemas.material import MaterialDetailResponse


class DirectoryOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    slug: str
    type: str
    description: str | None
    status: str = "current"
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


class DirectoryBreadcrumb(BaseModel):
    id: uuid.UUID
    name: str
    slug: str

    model_config = {"from_attributes": True}


class DirectoryPreviewThumbnail(BaseModel):
    material_id: uuid.UUID
    # Signed WebP thumbnail; null when the material has no generated one.
    url: str | None


class BrowseDirectory(DirectoryOut):
    like_count: int = 0
    is_liked: bool = False
    is_favourited: bool = False
    child_directory_count: int | None = None
    child_material_count: int | None = None
    # Absent when signing failed: the client then asks /materials/{id}/thumbnail.
    preview_thumbnails: list[DirectoryPreviewThumbnail] | None = None


class BrowseListing(BaseModel):
    type: Literal["directory_listing"] = "directory_listing"
    directory: BrowseDirectory | None
    directories: list[BrowseDirectory]
    materials: list[MaterialDetailResponse]
    breadcrumbs: list[DirectoryBreadcrumb] = []


class BrowseMaterial(BaseModel):
    type: Literal["material"] = "material"
    material: MaterialDetailResponse
    breadcrumbs: list[DirectoryBreadcrumb] = []


BrowseResponse = Annotated[BrowseListing | BrowseMaterial, Field(discriminator="type")]


class DirectoryChildren(BaseModel):
    directories: list[BrowseDirectory]
    materials: list[MaterialDetailResponse]


class TreeDirectory(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    slug: str
    full_path: str
    child_directory_count: int
    child_material_count: int


class TreeMaterial(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    type: str
    file_name: str | None
    file_mime_type: str | None
    # Target of a link material, used to pick its icon.
    url: str | None


class TreeChildren(BaseModel):
    directories: list[TreeDirectory]
    materials: list[TreeMaterial]
