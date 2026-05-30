import asyncio
import io
import uuid
import zipfile
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.database import get_db
from app.core.redis import get_redis
from app.core.sse import register_topic_queue, sse_event_stream, unregister_topic_queue
from app.dependencies.auth import get_current_user, security
from app.models.material import Material
from app.models.user import User
from app.schemas.directory import DirectoryBreadcrumb, DirectoryOut
from app.services import directory as directory_service

router = APIRouter(prefix="/api/directories", tags=["directories"])


_CHUNK_SIZE = 50


class DownloadChunk(BaseModel):
    url: str
    filename: str


class DownloadChunksResponse(BaseModel):
    dir_name: str
    chunks: list[DownloadChunk]


class ResolvePathsRequest(BaseModel):
    directory_ids: list[uuid.UUID] = []
    material_ids: list[uuid.UUID] = []


class MaterialResolveOut(BaseModel):
    directory_id: str | None
    title: str


class ResolvePathsResponse(BaseModel):
    directories: dict[uuid.UUID, list[DirectoryBreadcrumb]] = {}
    materials: dict[uuid.UUID, MaterialResolveOut] = {}


@router.post("/resolve-paths", response_model=ResolvePathsResponse)
async def resolve_paths(
    req: ResolvePathsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ResolvePathsResponse:
    resp = ResolvePathsResponse()

    if req.material_ids:
        mat_stmt = select(Material.id, Material.directory_id, Material.title).where(
            Material.id.in_(req.material_ids)
        )
        mat_res = await db.execute(mat_stmt)
        dir_ids_from_mat = []
        for row in mat_res.all():
            resp.materials[row.id] = MaterialResolveOut(
                directory_id=str(row.directory_id) if row.directory_id else None,
                title=row.title,
            )
            if row.directory_id:
                dir_ids_from_mat.append(row.directory_id)
        req.directory_ids.extend(dir_ids_from_mat)

    if req.directory_ids:

        async def fetch_dir_path(did: uuid.UUID) -> tuple[uuid.UUID, list[DirectoryBreadcrumb]]:
            try:
                p = await directory_service.get_directory_path(db, did)
                return did, [DirectoryBreadcrumb.model_validate(x) for x in p]
            except Exception:
                return did, []

        unique_dir_ids = set(req.directory_ids)
        dir_paths = await asyncio.gather(*(fetch_dir_path(did) for did in unique_dir_ids))
        for did, p in dir_paths:
            if p:
                resp.directories[did] = p

    return resp


@router.get("/{id}", response_model=DirectoryOut)
async def get_directory(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DirectoryOut:
    directory = await directory_service.get_directory_by_id(db, id)
    return DirectoryOut.model_validate(directory)


@router.get("/{id}/children")
async def get_directory_children(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict:  # type: ignore[type-arg]
    return await directory_service.get_directory_children(db, id)


@router.get("/{id}/path", response_model=list[DirectoryBreadcrumb])
async def get_directory_path(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[DirectoryBreadcrumb]:
    path = await directory_service.get_directory_path(db, id)
    return [DirectoryBreadcrumb.model_validate(p) for p in path]


@router.get("/{id}/sse")
async def directory_event_stream(id: str) -> EventSourceResponse:
    queue = register_topic_queue(id)
    return EventSourceResponse(
        sse_event_stream(queue, cleanup=lambda: unregister_topic_queue(id, queue)),
        headers={"X-Accel-Buffering": "no"},
    )


async def _generate_zip(entries: list[tuple[str, str]]) -> AsyncGenerator[bytes, None]:
    """Stream a ZIP file for the given (arcname, file_key) pairs.

    Each file is fetched from S3 and added to the ZIP sequentially.  New bytes
    are yielded to the client after each entry so the connection stays alive and
    the browser can show download progress.
    """
    from app.core.storage import stream_object

    class _Buf:
        """Writable BytesIO wrapper that tracks how many bytes have been flushed."""

        def __init__(self) -> None:
            self._b = io.BytesIO()
            self._flushed = 0

        def write(self, data: bytes) -> int:
            self._b.write(data)
            return len(data)

        def tell(self) -> int:
            return self._b.tell()

        def seek(self, pos: int, whence: int = 0) -> int:
            return self._b.seek(pos, whence)

        def flush(self) -> None:
            pass

        def pop(self) -> bytes:
            cur = self._b.tell()
            self._b.seek(self._flushed)
            chunk = self._b.read()
            self._flushed = cur
            return chunk

    buf = _Buf()
    zf = zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)  # type: ignore[call-overload]
    try:
        for arcname, file_key in entries:
            try:
                data = bytearray()
                async with stream_object(file_key) as body:
                    while True:
                        chunk = await body.read(65536)
                        if not chunk:
                            break
                        data.extend(chunk)
                zf.writestr(arcname, bytes(data))
            except Exception as exc:
                import logging

                logging.getLogger("wikint").warning(
                    "Failed to stream ZIP entry for key %s: %s", file_key, exc, exc_info=True
                )
                continue
            new_bytes = buf.pop()
            if new_bytes:
                yield new_bytes

        zf.close()
        final = buf.pop()
        if final:
            yield final
    finally:
        if zf.fp is not None:
            zf.close()


@router.get("/root/download-chunks", response_model=DownloadChunksResponse)
async def download_root_chunks(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,  # type: ignore[type-arg]
) -> DownloadChunksResponse:
    """Return Worker ZIP chunk URLs for the entire root level (all top-level directories)."""
    from app.core.exceptions import BadRequestError
    from app.dependencies.rate_limit import rate_limit_downloads

    if redis is None:
        from app.core.redis import redis_client

        redis = redis_client

    try:
        dir_name, entries = await directory_service.get_root_download_entries(db)
    except ValueError as exc:
        raise BadRequestError(str(exc))

    if not entries:
        raise BadRequestError("This directory contains no downloadable files.")

    await rate_limit_downloads(request, current_user, db, redis)
    return DownloadChunksResponse(dir_name=dir_name, chunks=[])


@router.get("/{id}/download-chunks", response_model=DownloadChunksResponse)
async def download_directory_chunks(
    id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,  # type: ignore[type-arg]
) -> DownloadChunksResponse:
    """Return a list of Worker URLs for downloading a directory as one or more ZIP files.

    Each chunk covers at most ``_CHUNK_SIZE`` files, keeping the Worker within the
    free-plan subrequest cap (50) and URL-length limit.  When the Worker is not
    configured the response contains an empty ``chunks`` list; the client should
    fall back to the streaming ``/download`` endpoint in that case.
    """
    from app.core.exceptions import BadRequestError
    from app.dependencies.rate_limit import rate_limit_downloads

    if redis is None:
        from app.core.redis import redis_client

        redis = redis_client

    try:
        dir_name, entries = await directory_service.get_directory_download_entries(db, id)
    except ValueError as exc:
        raise BadRequestError(str(exc))

    if not entries:
        raise BadRequestError("This directory contains no downloadable files.")

    # Rate-limit as a single download before falling back.
    await rate_limit_downloads(request, current_user, db, redis)
    return DownloadChunksResponse(dir_name=dir_name, chunks=[])


@router.get("/{id}/download")
async def download_directory_zip(
    id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    token: Annotated[str | None, Query()] = None,
    redis: Annotated[Redis | None, Depends(get_redis)] = None,  # type: ignore[type-arg]
) -> StreamingResponse:
    """Stream all files in a directory (recursively) as a single ZIP archive.

    Accepts auth via ``Authorization: Bearer`` header or ``?token=`` query param
    so that a plain browser link (window.location.href) can trigger the download.

    When ``WORKER_ZIP_URL`` is configured the heavy work (fetching from R2 and
    assembling the ZIP) is offloaded to a Cloudflare Worker.  The API only does
    auth + DB work and then issues a redirect carrying a short-lived HMAC-signed
    token so the Worker can verify the request without calling back to the API.
    """
    from app.core.exceptions import BadRequestError, UnauthorizedError
    from app.dependencies.auth import get_user_from_token
    from app.dependencies.rate_limit import rate_limit_downloads

    if redis is None:
        from app.core.redis import redis_client

        redis = redis_client

    effective_user: User | None = None
    if credentials is not None:
        try:
            effective_user = await get_user_from_token(db, redis, credentials.credentials)
        except Exception:
            pass
    if not effective_user and token:
        try:
            effective_user = await get_user_from_token(db, redis, token)
        except Exception:
            pass
    if not effective_user:
        raise UnauthorizedError()

    await rate_limit_downloads(request, effective_user, db, redis)

    try:
        dir_name, entries = await directory_service.get_directory_download_entries(db, id)
    except ValueError as exc:
        raise BadRequestError(str(exc))

    if not entries:
        raise BadRequestError("This directory contains no downloadable files.")

    # Zip streaming is now fully handled by the backend directly below.

    safe_name = dir_name.encode("ascii", "replace").decode("ascii").replace("/", "_") or "directory"
    from urllib.parse import quote

    encoded_name = quote(dir_name)
    disposition = f"attachment; filename=\"{safe_name}.zip\"; filename*=UTF-8''{encoded_name}.zip"

    return StreamingResponse(
        _generate_zip(entries),
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )


@router.post("/{id}/like")
async def like_directory(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    liked = await directory_service.toggle_directory_like(db, user.id, id)
    await db.commit()
    return {"liked": liked}


@router.post("/{id}/favourite")
async def favourite_directory(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    favourited = await directory_service.toggle_directory_favourite(db, user.id, id)
    await db.commit()
    return {"favourited": favourited}
