import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.exceptions import NotFoundError, UnauthorizedError
from app.core.database.database import get_db
from app.core.storage.facade import stream_object
from app.dependencies.auth import CurrentUser
from app.services.material import get_material_file_info, get_material_with_version

router = APIRouter(prefix="/api/eurooffice", tags=["eurooffice"])

# Maps file extensions to EuroOffice documentType
_EXT_TO_DOCTYPE: dict[str, str] = {
    "docx": "word",
    "doc": "word",
    "odt": "word",
    "xlsx": "cell",
    "xls": "cell",
    "ods": "cell",
    "pptx": "slide",
    "ppt": "slide",
    "pdf": "pdf",
}

_ALGORITHM = "HS256"


def _create_file_token(material_id: str) -> str:
    """Create a short-lived JWT for EuroOffice to fetch a specific file."""
    expire = datetime.now(UTC) + timedelta(seconds=settings.eurooffice_file_token_ttl)
    payload = {
        "sub": material_id,
        "type": "eurooffice_file",
        "exp": expire,
    }
    return jwt.encode(payload, settings.eurooffice_file_token_secret, algorithm=_ALGORITHM)


def _verify_file_token(token: str, material_id: str) -> bool:
    """Validate a file-access JWT signature and claims."""
    try:
        payload = jwt.decode(
            token,
            settings.eurooffice_file_token_secret,
            algorithms=[_ALGORITHM],
        )
        return payload.get("sub") == material_id and payload.get("type") == "eurooffice_file"
    except jwt.PyJWTError:
        return False


@router.get("/config/{material_id}")
async def get_eurooffice_config(
    material_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """
    Return a signed EuroOffice editor configuration for the given material.
    Called by the frontend (authenticated with user JWT).
    """
    material_id_str = str(material_id)
    data = await get_material_with_version(db, material_id_str)
    version = data.get("current_version_info")
    if version is None or version.get("file_key") is None:
        raise NotFoundError("No file available for preview")

    file_name: str = version.get("file_name") or ""
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    doc_type = _EXT_TO_DOCTYPE.get(ext, "word")

    # Short-lived token for EuroOffice to fetch the file via the internal API.
    # Embedded in the query string because EuroOffice's file downloader does not
    # forward custom requestHeaders — it only sends its own JWT.  This is an
    # internal container-to-container URL, never exposed to the browser.
    file_token = _create_file_token(material_id_str)
    file_url = f"{settings.eurooffice_internal_api_base_url}/api/eurooffice/file/{material_id_str}?token={file_token}"

    # Cache key: version_number invalidates on new uploads.
    doc_key = f"{material_id_str}-v{version['version_number']}"
    config: dict[str, Any] = {
        "documentType": doc_type,
        "document": {
            "fileType": ext,
            "key": doc_key,
            "title": file_name,
            "url": file_url,
            "permissions": {
                "edit": False,
                "download": True,  # Needed internally for some editor features
                "print": True,
                "comment": False,
                "review": False,
                "fillForms": True,
                "modifyContentControl": True,
                "modifyFilter": True,
                "chat": False,
                "copy": True,
            },
        },
        "editorConfig": {
            "mode": "view",
            "lang": "en",
            "user": {
                "id": str(user.id),
                "name": user.display_name or user.email,
                # Remove relative image URL to avoid cross-origin permission warnings
            },
            "customization": {
                "compactHeader": True,
                "compactToolbar": True,
                "hideRightMenu": True,
                "help": False,
                "plugins": False,
                "toolbarHideFileName": True,
                "anonymous": {"request": False},
                "logo": {
                    "image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
                    "imageEmbedded": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
                },
                "features": {
                    "tabStyle": "compact",
                },
                "layout": {
                    "toolbar": {
                        "file": False,
                        "collaboration": False,
                    }
                },
            },
        },
    }

    # Sign the entire config — EuroOffice validates this token before rendering
    config["token"] = jwt.encode(config, settings.eurooffice_jwt_secret, algorithm=_ALGORITHM)

    return config


# GET and HEAD are registered separately with distinct operation_ids: a single
# multi-method route makes FastAPI emit the same operationId for both methods,
# which produces duplicate identifiers in the generated TS client (openapi-typescript).
@router.get("/file/{material_id}", operation_id="serve_file_to_eurooffice")
@router.head("/file/{material_id}", operation_id="serve_file_to_eurooffice_head")
async def serve_file_to_eurooffice(
    request: Request,
    material_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """
    Serve the raw file bytes to EuroOffice Document Server.
    Called internally by EuroOffice (not the browser).
    Authenticated via a short-lived scoped JWT passed as ?token= query param
    (fallback: X-OO-File-Token header).

    EuroOffice probes the URL with HEAD before downloading and retries failed
    GETs with the same token — both methods must return 2xx.  We rely on the
    JWT expiry (60 s) rather than single-use JTI enforcement so retries work.
    """
    material_id_str = str(material_id)
    token = request.query_params.get("token") or request.headers.get("X-OO-File-Token")
    if not token:
        raise UnauthorizedError()

    if not _verify_file_token(token, material_id_str):
        raise UnauthorizedError()

    version = await get_material_file_info(db, material_id)
    if version.file_key is None:
        raise NotFoundError("No file available")

    file_name: str = version.file_name or "document"
    mime_type: str = version.file_mime_type or "application/octet-stream"

    ascii_safe = (
        file_name.encode("ascii", errors="replace")
        .decode("ascii")
        .replace('"', "_")
        .replace("\r", "")
        .replace("\n", "")
    )
    encoded = quote(file_name, safe="")

    if request.method == "HEAD":
        return Response(media_type=mime_type)

    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_safe}\"; filename*=UTF-8''{encoded}",
    }

    async def _iter_file(key: str) -> AsyncIterator[bytes]:
        async with stream_object(key) as body:
            chunk = await body.read(65536)
            while chunk:
                yield chunk
                chunk = await body.read(65536)

    return StreamingResponse(_iter_file(version.file_key), media_type=mime_type, headers=headers)
