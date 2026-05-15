import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Discriminator, Field, Tag, field_validator

from app.core.sanitization import NameStr, SanitizedStr, strip_null_chars
from app.models.security import VirusScanResult
from app.schemas.user import UserOut

# ---------------------------------------------------------------------------
# Shared validation constants
# ---------------------------------------------------------------------------

ALLOWED_MATERIAL_TYPES = {
    "polycopie",
    "annal",
    "cheatsheet",
    "tip",
    "review",
    "discussion",
    "video",
    "document",
    "other",
    "qcm",
}
ALLOWED_DIRECTORY_TYPES = {"folder", "course", "year", "semester", "other"}
MAX_TAGS = 20
MAX_TAG_LENGTH = 20
MAX_METADATA_KEYS = 20
MAX_FILE_NAME_LENGTH = 255


def _validate_tags(tags: list[str] | None) -> list[str] | None:
    """Validate tag list: limit count, individual length, strip whitespace."""
    if tags is None:
        return None
    if len(tags) > MAX_TAGS:
        raise ValueError(f"Too many tags (max {MAX_TAGS})")
    out: list[str] = []
    for t in tags:
        t = t.strip()
        if not t:
            continue
        if len(t) > MAX_TAG_LENGTH:
            raise ValueError(f"Tag too long (max {MAX_TAG_LENGTH} chars)")
        out.append(t)
    return out


def _validate_metadata(metadata: dict[str, object] | None) -> dict[str, object] | None:
    """Limit metadata size to prevent abuse."""
    if metadata is None:
        return None
    if len(metadata) > MAX_METADATA_KEYS:
        raise ValueError(f"Too many metadata keys (max {MAX_METADATA_KEYS})")
    return metadata


def _validate_file_key(file_key: str | None) -> str | None:
    """Ensure file_key is a plausible uploads/ or cas/ path — no traversal."""
    if file_key is None:
        return None
    if not (file_key.startswith("uploads/") or file_key.startswith("cas/")):
        raise ValueError("file_key must start with uploads/ or cas/")
    if ".." in file_key or "\x00" in file_key:
        raise ValueError("Invalid file_key")
    return file_key


def _validate_file_name(file_name: str | None) -> str | None:
    if file_name is None:
        return None
    if len(file_name) > MAX_FILE_NAME_LENGTH:
        raise ValueError(f"file_name too long (max {MAX_FILE_NAME_LENGTH})")
    if "/" in file_name or "\\" in file_name or "\x00" in file_name:
        raise ValueError("Invalid characters in file_name")
    return file_name


# ---------------------------------------------------------------------------
# Individual operation payloads (discriminated on `op`)
# ---------------------------------------------------------------------------


class AttachmentOp(BaseModel):
    title: NameStr = Field(..., min_length=1, max_length=128, pattern=r"^\s*\S.*$")
    type: str
    file_key: str | None = None
    file_name: str | None = None
    file_size: int | None = Field(None, ge=0)
    file_mime_type: str | None = Field(None, max_length=200)
    content_sha256: str | None = Field(None, max_length=64)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ALLOWED_MATERIAL_TYPES:
            raise ValueError(f"Invalid material type: {v}")
        return v

    @field_validator("file_key")
    @classmethod
    def validate_file_key(cls, v: str | None) -> str | None:
        return _validate_file_key(v)

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, v: str | None) -> str | None:
        return _validate_file_name(v)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        return _validate_tags(v) or []

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v: dict[str, object]) -> dict[str, object]:
        return _validate_metadata(v) or {}


class CreateMaterialOp(BaseModel):
    op: Literal["create_material"] = "create_material"
    temp_id: str | None = Field(
        None,
        pattern=r"^\$",
        max_length=50,
        description="Client-assigned temp ID starting with $ for inter-op references",
    )
    directory_id: uuid.UUID | str | None = Field(
        None,
        description="Real UUID or $temp-id of a directory created in this batch (None for root)",
    )
    title: NameStr = Field(..., min_length=1, max_length=128, pattern=r"^\s*\S.*$")
    type: str
    description: SanitizedStr | None = Field(None, max_length=1000)
    tags: list[str] = Field(default_factory=list)
    file_key: str | None = None
    file_name: str | None = None
    file_size: int | None = Field(None, ge=0)  # upper bound enforced via MAX_FILE_SIZE_MB setting
    file_mime_type: str | None = Field(None, max_length=200)
    content_sha256: str | None = Field(None, max_length=64)
    metadata: dict[str, object] = Field(default_factory=dict)
    parent_material_id: uuid.UUID | str | None = None
    attachments: list[AttachmentOp] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ALLOWED_MATERIAL_TYPES:
            raise ValueError(f"Invalid material type: {v}")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        return _validate_tags(v) or []

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v: dict[str, object]) -> dict[str, object]:
        return _validate_metadata(v) or {}

    @field_validator("file_key")
    @classmethod
    def validate_file_key(cls, v: str | None) -> str | None:
        return _validate_file_key(v)

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, v: str | None) -> str | None:
        return _validate_file_name(v)


class EditMaterialOp(BaseModel):
    op: Literal["edit_material"] = "edit_material"
    material_id: uuid.UUID | str
    title: NameStr | None = Field(None, min_length=1, max_length=128, pattern=r"^\s*\S.*$")
    type: str | None = None
    description: SanitizedStr | None = Field(None, max_length=1000)
    tags: list[str] | None = None
    file_key: str | None = None
    file_name: str | None = None
    file_size: int | None = Field(None, ge=0)  # upper bound enforced via MAX_FILE_SIZE_MB setting
    file_mime_type: str | None = Field(None, max_length=200)
    content_sha256: str | None = Field(None, max_length=64)
    diff_summary: str | None = Field(None)
    metadata: dict[str, object] | None = None
    version_lock: int | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_MATERIAL_TYPES:
            raise ValueError(f"Invalid material type: {v}")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v: dict[str, object] | None) -> dict[str, object] | None:
        return _validate_metadata(v)

    @field_validator("file_key")
    @classmethod
    def validate_file_key(cls, v: str | None) -> str | None:
        return _validate_file_key(v)

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, v: str | None) -> str | None:
        return _validate_file_name(v)


class DeleteMaterialOp(BaseModel):
    op: Literal["delete_material"] = "delete_material"
    material_id: uuid.UUID | str


class CreateDirectoryOp(BaseModel):
    op: Literal["create_directory"] = "create_directory"
    temp_id: str | None = Field(
        None,
        pattern=r"^\$",
        max_length=50,
        description="Client-assigned temp ID starting with $ for inter-op references",
    )
    parent_id: uuid.UUID | str | None = None
    name: NameStr = Field(..., min_length=1, max_length=128, pattern=r"^\s*\S.*$")
    type: str = "folder"
    description: SanitizedStr | None = Field(None, max_length=1000)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ALLOWED_DIRECTORY_TYPES:
            raise ValueError(f"Invalid directory type: {v}")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        return _validate_tags(v) or []

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v: dict[str, object]) -> dict[str, object]:
        return _validate_metadata(v) or {}


class EditDirectoryOp(BaseModel):
    op: Literal["edit_directory"] = "edit_directory"
    directory_id: uuid.UUID | str
    name: NameStr | None = Field(None, min_length=1, max_length=128, pattern=r"^\s*\S.*$")
    type: str | None = None
    description: SanitizedStr | None = Field(None, max_length=1000)
    tags: list[str] | None = None
    metadata: dict[str, object] | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_DIRECTORY_TYPES:
            raise ValueError(f"Invalid directory type: {v}")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v: dict[str, object] | None) -> dict[str, object] | None:
        return _validate_metadata(v)


class DeleteDirectoryOp(BaseModel):
    op: Literal["delete_directory"] = "delete_directory"
    directory_id: uuid.UUID | str


class MoveItemOp(BaseModel):
    op: Literal["move_item"] = "move_item"
    target_type: Literal["directory", "material"]
    target_id: uuid.UUID | str
    new_parent_id: uuid.UUID | str | None
    # Enrichment for ghost rendering in PR previews
    target_name: str | None = Field(None, max_length=128)
    target_title: str | None = Field(None, max_length=128)
    target_material_type: str | None = None
    version_lock: int | None = None

    @field_validator("target_material_type")
    @classmethod
    def validate_target_material_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_MATERIAL_TYPES:
            raise ValueError(f"Invalid material type: {v}")
        return v


def _get_op_discriminator(v: dict[str, object] | BaseModel) -> str:
    if isinstance(v, dict):
        res = v.get("op", "")
        return str(res)
    return str(getattr(v, "op", ""))


Operation = Annotated[
    Annotated[CreateMaterialOp, Tag("create_material")]
    | Annotated[EditMaterialOp, Tag("edit_material")]
    | Annotated[DeleteMaterialOp, Tag("delete_material")]
    | Annotated[CreateDirectoryOp, Tag("create_directory")]
    | Annotated[EditDirectoryOp, Tag("edit_directory")]
    | Annotated[DeleteDirectoryOp, Tag("delete_directory")]
    | Annotated[MoveItemOp, Tag("move_item")],
    Discriminator(_get_op_discriminator),
]

# Keep legacy aliases for migration period / reading old data
CreateMaterialPayload = CreateMaterialOp
EditMaterialPayload = EditMaterialOp
DeleteMaterialPayload = DeleteMaterialOp
CreateDirectoryPayload = CreateDirectoryOp
EditDirectoryPayload = EditDirectoryOp
DeleteDirectoryPayload = DeleteDirectoryOp
MoveItemPayload = MoveItemOp

MAX_OPERATIONS = 50


class PullRequestCreate(BaseModel):
    title: SanitizedStr = Field(..., min_length=3, max_length=300, pattern=r"^\s*\S.*$")
    description: SanitizedStr | None = Field(None, max_length=1000)
    operations: Annotated[list[Operation], BeforeValidator(strip_null_chars)] = Field(
        ..., min_length=1
    )

    @field_validator("operations")
    @classmethod
    def validate_temp_ids_unique(cls, v: list[Operation]) -> list[Operation]:
        temp_ids: list[str] = []
        for op in v:
            tid = getattr(op, "temp_id", None)
            if tid:
                if tid in temp_ids:
                    raise ValueError(f"Duplicate temp_id: {tid}")
                temp_ids.append(tid)
        return v


class PullRequestOut(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    title: str
    description: str | None
    payload: list[dict[str, object]]
    applied_result: list[dict[str, object]] | None = None
    summary_types: list[str] = Field(default_factory=list)
    author_id: uuid.UUID | None
    reviewed_by: uuid.UUID | None
    virus_scan_result: VirusScanResult
    rejection_reason: str | None = None
    author: UserOut | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    approved_at: datetime | None = None
    reverts_pr_id: uuid.UUID | None = None
    reverted_by_pr_id: uuid.UUID | None = None
    revert_grace_expires_at: datetime | None = None
    can_revert: bool = False

    model_config = {"from_attributes": True}

    @classmethod
    def model_validate(cls, obj: object, **kwargs: object) -> "PullRequestOut":
        instance = super().model_validate(obj)
        if hasattr(obj, "is_revertable"):
            instance.can_revert = obj.is_revertable
        if hasattr(obj, "revert_grace_expires_at"):
            instance.revert_grace_expires_at = obj.revert_grace_expires_at
        return instance


class RejectRequest(BaseModel):
    reason: SanitizedStr = Field(..., min_length=10, max_length=1000)


class PRCommentCreate(BaseModel):
    body: SanitizedStr = Field(..., min_length=1, max_length=1000)
    parent_id: uuid.UUID | None = None


class PRCommentOut(BaseModel):
    id: uuid.UUID
    pr_id: uuid.UUID
    author_id: uuid.UUID | None
    body: str
    parent_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    author: UserOut | None

    model_config = {"from_attributes": True}
