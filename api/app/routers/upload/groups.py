"""Bounded admission groups for browser folder uploads."""

import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from app.core.common.batch_upload_limits import (
    BATCH_MAX_FILES,
    BATCH_MAX_FILES_PRIVILEGED,
    UPLOAD_GROUP_TTL_SECONDS,
)
from app.core.common.constants import PRIVILEGED_ROLES
from app.core.common.exceptions import BadRequestError, ForbiddenError
from app.core.common.upload_errors import UploadErrorCode
from app.core.database.redis import get_redis
from app.dependencies.auth import CurrentUser
from app.dependencies.rate_limit import UPLOAD_GROUP_KEY_PREFIX, rate_limit_uploads
from app.models.user import UserRole
from app.schemas.material import UploadGroupOut, UploadGroupRequest

router = APIRouter()


@router.post("/groups", response_model=UploadGroupOut, status_code=201)
async def create_upload_group(
    data: UploadGroupRequest,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    _: Annotated[None, Depends(rate_limit_uploads)],
) -> UploadGroupOut:
    """Admit one folder while keeping every contained upload independently retryable."""
    if user.role == UserRole.GUEST:
        raise ForbiddenError("Guest uploads cannot use folder admission groups")

    limit = BATCH_MAX_FILES_PRIVILEGED if user.role in PRIVILEGED_ROLES else BATCH_MAX_FILES
    if data.file_count > limit:
        raise BadRequestError(
            f"Folder contains {data.file_count} files; maximum allowed is {limit}.",
            code=UploadErrorCode.BATCH_TOO_LARGE,
        )

    group_id = str(uuid4())
    payload = json.dumps({"user_id": str(user.id), "max_files": data.file_count})
    await redis.set(
        f"{UPLOAD_GROUP_KEY_PREFIX}{group_id}",
        payload,
        ex=UPLOAD_GROUP_TTL_SECONDS,
    )
    return UploadGroupOut(
        group_id=group_id,
        max_files=data.file_count,
        expires_in=UPLOAD_GROUP_TTL_SECONDS,
    )
