from pydantic import BaseModel, Field, field_serializer, field_validator

from app.services.avatar import is_safe_avatar_reference

# OTP codes are 8 alphanumeric uppercase chars (alphabet excludes I, O, 1, 0)
_OTP_PATTERN = r"^[A-Z2-9]{8}$"
_MAGIC_TOKEN_MAX = 128


def _validate_email_format(v: str) -> str:
    """Format-only checks — domain policy is enforced asynchronously in the service layer."""
    v = v.strip().lower()
    if len(v) > 254:
        raise ValueError("Email too long")
    if "+" in v:
        raise ValueError("Email aliases with '+' are not allowed")
    if "@" not in v:
        raise ValueError("Invalid email address")
    return v


class RequestCodeIn(BaseModel):
    email: str = Field(..., max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email_format(v)


class VerifyCodeIn(BaseModel):
    email: str = Field(..., max_length=254)
    code: str = Field(..., min_length=8, max_length=8, pattern=_OTP_PATTERN)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email_format(v)


class VerifyMagicLinkIn(BaseModel):
    token: str = Field(..., min_length=1, max_length=_MAGIC_TOKEN_MAX)


class GoogleLoginIn(BaseModel):
    credential: str = Field(..., min_length=1)


class LoginIn(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email_format(v)


class SetupIn(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)
    bootstrap_token: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9A-Fa-f]{64}$"
    )

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email_format(v)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class TokenResponse(BaseModel):
    access_token: str
    user: "UserBrief"
    is_new_user: bool


class RefreshResponse(BaseModel):
    access_token: str
    user: "UserBrief | None" = None


class UserBrief(BaseModel):
    id: str
    email: str
    display_name: str | None
    avatar_url: str | None
    role: str
    onboarded: bool
    auto_approve: bool

    @field_serializer("avatar_url")
    def serialize_avatar_url(self, value: str | None) -> str | None:
        return value if is_safe_avatar_reference(value, self.id) else None

    model_config = {"from_attributes": True}
