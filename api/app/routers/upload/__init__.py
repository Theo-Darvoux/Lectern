"""Upload router package -- aggregates sub-routers into a single router."""

from fastapi import APIRouter

from app.routers.upload.batch_zip import router as batch_zip_router
from app.routers.upload.direct import upload_file
from app.routers.upload.file_status import router as file_status_router
from app.routers.upload.groups import router as groups_router
from app.routers.upload.presigned import router as presigned_router
from app.routers.upload.status import router as status_router
from app.schemas.material import UploadPendingOut

__all__ = ["router"]

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
router.include_router(file_status_router)
router.include_router(status_router)
