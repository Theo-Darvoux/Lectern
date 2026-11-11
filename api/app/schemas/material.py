import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from app.models.content_status import ContentStatus
from app.models.security import VirusScanResult


class MaterialVersionOut(BaseModel):
    id: uuid.UUID
    material_id: uuid.UUID
    version_number: int
    file_key: str | None
    file_name: str | None
    file_size: int | None
    file_mime_type: str | None
    diff_summary: str | None
    author_id: uuid.UUID | None
    is_anonymous: bool = False
    pr_id: uuid.UUID | None
    virus_scan_result: VirusScanResult
    version_lock: int
    created_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def hide_anonymous_author(self) -> "MaterialVersionOut":
        if self.is_anonymous:
            self.author_id = None
        return self


class PublicMaterialVersionOut(BaseModel):
    version_number: int
    file_name: str | None
    file_size: int | None
    file_mime_type: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


MaterialVersionResponse = MaterialVersionOut | PublicMaterialVersionOut


def project_material_version(value: object, *, public: bool) -> MaterialVersionResponse:
    """Project one material version through the member or public boundary."""
    schema = PublicMaterialVersionOut if public else MaterialVersionOut
    return schema.model_validate(value)


class MaterialOut(BaseModel):
    id: uuid.UUID
    directory_id: uuid.UUID | None
    directory_path: str | None = None
    title: str
    slug: str
    description: str | None
    type: str
    status: ContentStatus = ContentStatus.CURRENT
    current_version: int
    parent_material_id: uuid.UUID | None
    author_id: uuid.UUID | None
    is_anonymous: bool = False
    metadata: dict[str, object] = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    download_count: int
    total_views: int
    views_today: int
    like_count: int
    is_liked: bool = False
    is_favourited: bool = False
    attachment_count: int = 0
    comment_count: int = 0
    annotation_count: int = 0
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def extract_tag_names(cls, v: list[object] | None) -> list[str]:
        if not v:
            return []
        return [tag.name if hasattr(tag, "name") else str(tag) for tag in v]

    @model_validator(mode="after")
    def hide_anonymous_author(self) -> "MaterialOut":
        # Anonymous materials keep author_id in the database for moderation only.
        if self.is_anonymous:
            self.author_id = None
        return self

    model_config = {"from_attributes": True}


class MaterialDetail(MaterialOut):
    current_version_info: MaterialVersionOut | None = None


class PublicMaterialDetail(MaterialOut):
    current_version_info: PublicMaterialVersionOut | None = None


MaterialDetailResponse = MaterialDetail | PublicMaterialDetail


def project_material_detail(value: object, *, public: bool) -> MaterialDetailResponse:
    """Project one material through the member or public version boundary."""
    schema = PublicMaterialDetail if public else MaterialDetail
    return schema.model_validate(value)


class UploadStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    CLEAN = "clean"
    MALICIOUS = "malicious"
    FAILED = "failed"
    APPLIED = "applied"


# ── Upload pipeline response schemas ──


class UploadPendingOut(BaseModel):
    """Immediate response from POST /upload or POST /upload/complete.

    The file is in quarantine and being processed asynchronously.
    Follow the ``uploads`` channel of /events/sse, or poll
    /upload/status/{file_key}, for the final status.
    """

    upload_id: str
    file_key: str
    status: UploadStatus
    size: int
    mime_type: str


class UploadCompleteOut(BaseModel):
    """Final result delivered inside the SSE 'clean' event."""

    file_key: str
    file_name: str | None = None
    size: int
    original_size: int
    mime_type: str


class UploadStatusOut(BaseModel):
    """SSE event payload published by the background worker.

    V2 clients should use ``overall_percent`` for progress display.
    The ``detail`` string is preserved for backward compatibility but must not be
    parsed for progress mapping — it is a human-readable label only.
    """

    upload_id: str | None = None
    file_key: str
    status: UploadStatus
    detail: str | None = None
    result: UploadCompleteOut | None = None
    # V2: structured numeric progress — all optional so older workers stay compatible
    stage_index: int | None = None  # 0-based index of the current pipeline stage
    stage_total: int | None = None  # total number of pipeline stages
    stage_percent: float | None = None  # completion within the current stage [0.0, 1.0]
    overall_percent: float | None = None  # overall pipeline completion [0.0, 1.0]
    stage: str | None = None  # stable stage name, for client-side translation
    error_code: str | None = None  # UploadErrorCode of a terminal failure


# ── Presigned upload schemas ──


class UploadInitRequest(BaseModel):
    """Body for POST /upload/init — requests a presigned PUT URL."""

    filename: str = Field(..., min_length=1, max_length=255)
    size: int = Field(..., ge=0)
    mime_type: str = Field("application/octet-stream", max_length=200)
    sha256: str | None = None

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, v: str | None) -> str | None:
        if v is not None:
            if len(v) != 64:
                raise ValueError("sha256 must be exactly 64 characters long")
            try:
                bytes.fromhex(v)
            except ValueError:
                raise ValueError("sha256 must be a valid hex string")
        return v


class PresignedUploadOut(BaseModel):
    """Response from POST /upload/init."""

    quarantine_key: str
    upload_id: str
    presigned_url: str
    expires_in: int


class UploadCompleteRequest(BaseModel):
    """Body for POST /upload/complete — confirm a presigned upload and start processing."""

    quarantine_key: str
    upload_id: str


# ── Batch-zip schemas ─────────────────────────────────────────────────────────


class BatchZipEntry(BaseModel):
    """One successfully extracted file from a batch-zip upload."""

    filename: str
    relative_path: str
    quarantine_key: str
    upload_id: str
    size: int
    mime_type: str


class BatchZipResponse(BaseModel):
    """Response from POST /upload/batch-zip."""

    batch_id: str
    files: list[BatchZipEntry]
    skipped: int
    errors: list[str]


class UploadGroupRequest(BaseModel):
    """Reserve a bounded set of per-file admissions for one folder."""

    file_count: int = Field(ge=1)


class UploadGroupOut(BaseModel):
    """Server-issued capability for a bounded folder upload."""

    group_id: str
    max_files: int
    expires_in: int


# ── Upload history ───────────────────────────────────────────────────────────


class UploadHistoryItem(BaseModel):
    """One row from the user's upload history."""

    upload_id: str
    filename: str
    mime_type: str | None
    size_bytes: int | None
    status: str
    sha256: str | None
    final_key: str | None
    created_at: datetime
    updated_at: datetime


class UploadHistoryOut(BaseModel):
    items: list[UploadHistoryItem]
    total: int
    page: int
    pages: int


class PresignedMultipartPart(BaseModel):
    part_number: int
    size: int = Field(ge=1)
    # Exactly one is set: a URL for a part still to send, or the ETag of a stored part.
    url: str | None = None
    etag: str | None = None


class PresignedMultipartInitOut(BaseModel):
    quarantine_key: str
    upload_id: str
    s3_multipart_id: str
    parts: list[PresignedMultipartPart]
    expires_in: int


class S3PartETag(BaseModel):
    PartNumber: int = Field(ge=1, le=10_000)
    ETag: str = Field(min_length=1, max_length=256)

    model_config = {"extra": "forbid"}


class PresignedMultipartCompleteRequest(BaseModel):
    upload_id: str = Field(min_length=1, max_length=128)
    parts: list[S3PartETag] = Field(min_length=1, max_length=10_000)

    model_config = {"extra": "forbid"}
