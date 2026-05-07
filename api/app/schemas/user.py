import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.sanitization import SanitizedStr

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
    # avatar_url is set server-side after upload — clients may pass None to clear it.
    # Enforce https to block arbitrary scheme injection and cap length.
    avatar_url: str | None = Field(None, max_length=2048)
    auto_approve: bool | None = None

    @field_validator("academic_year")
    @classmethod
    def validate_academic_year(cls, v: str | None) -> str | None:
        if v is not None and v not in ACADEMIC_YEARS:
            raise ValueError("academic_year must be one of: 1A, 2A, 3A+")
        return v

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, v: str | None) -> str | None:
        if v is not None and not (
            v.startswith("https://")
            or v.startswith("cas/")
            or v.startswith("materials/")
            or v.startswith("quarantine/")
        ):
            raise ValueError(
                "avatar_url must be an https:// URL or a storage key (cas/, materials/, or quarantine/)"
            )
        return v


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
    created_at: datetime

    model_config = {"from_attributes": True}


class UserProfileOut(UserOut):
    prs_approved: int = 0
    prs_total: int = 0
    annotations_count: int = 0
    comments_count: int = 0
    open_pr_count: int = 0
    reputation: int = 0
