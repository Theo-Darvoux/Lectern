from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_serializer

from app.core.sanitization import SanitizedStr
from app.services.avatar import is_safe_avatar_reference

StrFromUUID = Annotated[str, BeforeValidator(lambda v: str(v))]
OptStrFromUUID = Annotated[str | None, BeforeValidator(lambda v: str(v) if v is not None else None)]


class CommentCreateIn(BaseModel):
    target_type: Literal["directory", "material"]
    target_id: str
    body: SanitizedStr = Field(min_length=1, max_length=1000)


class CommentUpdateIn(BaseModel):
    body: SanitizedStr = Field(min_length=1, max_length=1000)


class CommentAuthor(BaseModel):
    id: StrFromUUID
    display_name: str | None
    avatar_url: str | None

    @field_serializer("avatar_url")
    def serialize_avatar_url(self, value: str | None) -> str | None:
        return value if is_safe_avatar_reference(value, self.id) else None

    model_config = {"from_attributes": True}


class CommentOut(BaseModel):
    id: StrFromUUID
    target_type: str
    target_id: StrFromUUID
    author_id: OptStrFromUUID
    author: CommentAuthor | None = None
    body: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
