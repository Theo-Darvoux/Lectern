from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class FeaturedItemCreate(BaseModel):
    material_id: uuid.UUID | None = None
    directory_id: uuid.UUID | None = None
    title: str | None = None
    description: str | None = None
    start_at: datetime
    end_at: datetime
    priority: int = 0


class FeaturedItemUpdate(BaseModel):
    material_id: uuid.UUID | None = None
    directory_id: uuid.UUID | None = None
    title: str | None = None
    description: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    priority: int | None = None
