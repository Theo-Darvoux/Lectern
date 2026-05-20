import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from app.core.sanitization import SanitizedStr, sanitize_json_payload

StrFromUUID = Annotated[str, BeforeValidator(lambda v: str(v))]
OptStrFromUUID = Annotated[str | None, BeforeValidator(lambda v: str(v) if v is not None else None)]

MAX_POSITION_DATA_KEYS = 20


class AnnotationAuthor(BaseModel):
    id: StrFromUUID
    display_name: str | None
    avatar_url: str | None

    model_config = {"from_attributes": True}


class AnnotationCreateIn(BaseModel):
    body: SanitizedStr = Field(min_length=1, max_length=1000)
    selection_text: SanitizedStr | None = Field(None, max_length=1000)
    position_data: dict[str, object] | None = None
    page: int | None = Field(None, ge=0, le=100_000)
    reply_to_id: str | None = Field(None, max_length=36)

    @field_validator("reply_to_id")
    @classmethod
    def validate_reply_to_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("reply_to_id must be a valid UUID")
        return v

    @field_validator("position_data")
    @classmethod
    def validate_position_data(cls, v: dict[str, object] | None) -> dict[str, object] | None:
        if v is None:
            return None
        if len(v) > MAX_POSITION_DATA_KEYS:
            raise ValueError(f"position_data too large (max {MAX_POSITION_DATA_KEYS} keys)")
        return sanitize_json_payload(v)  # type: ignore[return-value]


class AnnotationUpdateIn(BaseModel):
    body: SanitizedStr = Field(min_length=1, max_length=1000)


class AnnotationOut(BaseModel):
    id: StrFromUUID
    material_id: StrFromUUID
    version_id: OptStrFromUUID
    author_id: OptStrFromUUID
    author: AnnotationAuthor | None = None
    body: str
    page: int | None
    selection_text: str | None
    position_data: dict[str, object] | None
    thread_id: OptStrFromUUID
    reply_to_id: OptStrFromUUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ThreadOut(BaseModel):
    root: AnnotationOut
    replies: list[AnnotationOut]
