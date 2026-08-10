import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from app.core.sanitization import SanitizedStr
from app.services.avatar import is_safe_avatar_reference

ACADEMIC_YEARS = ("1A", "2A", "3A+")


class OnboardIn(BaseModel):
    display_name: SanitizedStr = Field(..., min_length=1, max_length=64)
    academic_year: str
    gdpr_consent: bool

    @field_validator("academic_year")
    @classmethod
    def validate_academic_year(cls, v: str) -> str:
        if v not in ACADEMIC_YEARS:
            raise ValueError("academic_year must be one of: 1A, 2A, 3A+")
        return v


class UserUpdateIn(BaseModel):
    display_name: SanitizedStr | None = Field(None, min_length=1, max_length=64)
    bio: SanitizedStr | None = Field(None, max_length=500)
    academic_year: str | None = None
    # avatar_url is output/server-owned. PATCH may pass only null to clear it.
    # New avatars are adopted by owner-bound Upload.upload_id, never by storage key.
    avatar_url: None = None
    avatar_upload_id: uuid.UUID | None = None
    auto_approve: bool | None = None

    @field_validator("academic_year")
    @classmethod
    def validate_academic_year(cls, v: str | None) -> str | None:
        if v is not None and v not in ACADEMIC_YEARS:
            raise ValueError("academic_year must be one of: 1A, 2A, 3A+")
        return v

    @model_validator(mode="after")
    def validate_avatar_mutation(self) -> "UserUpdateIn":
        if "avatar_url" in self.model_fields_set and "avatar_upload_id" in self.model_fields_set:
            raise ValueError("avatar_url clear and avatar_upload_id cannot be supplied together")
        return self


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    avatar_url: str | None
    role: str
    bio: str | None
    academic_year: str | None
    onboarded: bool
    auto_approve: bool
    completed_tutorials: list[str] = []
    created_at: datetime

    @field_serializer("avatar_url")
    def serialize_avatar_url(self, value: str | None) -> str | None:
        return value if is_safe_avatar_reference(value, self.id) else None

    model_config = {"from_attributes": True}


class TutorialCompleteIn(BaseModel):
    tutorial_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")


class UserProfileOut(UserOut):
    prs_approved: int = 0
    prs_total: int = 0
    annotations_count: int = 0
    comments_count: int = 0
    open_pr_count: int = 0
    reputation: int = 0
