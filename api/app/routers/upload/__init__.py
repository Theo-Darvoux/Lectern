"""Upload router package -- aggregates sub-routers into a single router."""

from fastapi import APIRouter

from app.routers.upload.batch_zip import router as batch_zip_router
from app.routers.upload.direct import upload_file
from app.routers.upload.groups import router as groups_router
from app.routers.upload.helpers import (
    _FAST_QUEUE_NAME,
    _FAST_QUEUE_THRESHOLD,
    _QUOTA_KEY_PREFIX,
    _SLOW_QUEUE_NAME,
    _STATUS_CACHE_PREFIX,
    _UPLOAD_INTENT_PREFIX,
    LARGE_FILE_THRESHOLD,
    LARGE_SVG_THRESHOLD,
    MAX_PENDING_UPLOADS,
    _check_pending_cap,
    _create_upload_row,
    _enqueue_processing,
)
from app.routers.upload.presigned import router as presigned_router
from app.routers.upload.sse import router as sse_router
from app.routers.upload.sse import upload_events
from app.routers.upload.status import check_file_exists
from app.routers.upload.status import router as status_router
from app.routers.upload.validators import (
    _check_per_type_size,
    _validate_filename,
)
from app.schemas.material import UploadPendingOut

__all__ = [
    "LARGE_FILE_THRESHOLD",
    "LARGE_SVG_THRESHOLD",
    "MAX_PENDING_UPLOADS",
    "_FAST_QUEUE_NAME",
    "_FAST_QUEUE_THRESHOLD",
    "_QUOTA_KEY_PREFIX",
    "_SLOW_QUEUE_NAME",
    "_STATUS_CACHE_PREFIX",
    "_UPLOAD_INTENT_PREFIX",
    "_check_pending_cap",
    "_check_per_type_size",
    "_create_upload_row",
    "_enqueue_processing",
    "_validate_filename",
    "check_file_exists",
    "upload_events",
]

router = APIRouter(prefix="/api/upload", tags=["upload"])

# Register the root POST endpoint directly to avoid FastAPI's
# empty-prefix-and-empty-path restriction with include_router.
router.add_api_route(
    "",
    upload_file,
    methods=["POST"],
    response_model=UploadPendingOut,
    status_code=202,
)

router.include_router(batch_zip_router)
router.include_router(groups_router)
router.include_router(presigned_router)
router.include_router(sse_router)
router.include_router(status_router)
