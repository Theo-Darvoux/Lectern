import asyncio
import logging
import uuid
import zipfile
from collections.abc import AsyncGenerator
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.common.exceptions import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.core.database.database import get_db
from app.core.database.redis import get_redis, redis_client
from app.core.events.limiter import limiter
from app.core.events.sse import (
    broadcast_to_topic,
    register_topic_queue,
    sse_event_stream,
    topic_owner_keys,
    unregister_topic_queue,
)
from app.core.storage.facade import stream_object
from app.dependencies.auth import ReadUser, get_current_user
from app.dependencies.rate_limit import rate_limit_downloads
from app.models.material import Material
from app.models.user import User
from app.schemas.directory import DirectoryBreadcrumb, DirectoryOut
from app.services import directory as directory_service

logger = logging.getLogger(__name__)

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
) -> dict[str, Any]:
    return await directory_service.get_directory_children(db, id)


@router.get("/{id}/path", response_model=list[DirectoryBreadcrumb])
async def get_directory_path(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[DirectoryBreadcrumb]:
    path = await directory_service.get_directory_path(db, id)
    return [DirectoryBreadcrumb.model_validate(p) for p in path]


def _normalize_directory_topic(raw_id: str) -> str:
    if raw_id == "root":
        return "root"
    try:
        return str(uuid.UUID(raw_id))
    except ValueError:
        raise NotFoundError("Directory not found")


@router.get("/{id}/sse")
@limiter.limit("20/minute")
async def directory_event_stream(request: Request, id: str) -> EventSourceResponse:
    topic = _normalize_directory_topic(id)
    owner_keys = topic_owner_keys(
        client_host=request.client.host if request.client is not None else None,
        forwarded_for=request.headers.get("x-forwarded-for"),
        real_ip=request.headers.get("x-real-ip"),
    )
    queue = register_topic_queue(topic, owner_keys=owner_keys)
    return EventSourceResponse(
        sse_event_stream(queue, cleanup=lambda: unregister_topic_queue(topic, queue)),
        headers={"X-Accel-Buffering": "no"},
    )


async def _generate_zip(entries: list[tuple[str, str]]) -> AsyncGenerator[bytes, None]:
    """Stream a ZIP with bounded memory and storage backpressure.

    The sink is intentionally unseekable, so ``zipfile`` emits data descriptors
    and never needs the complete archive in memory. The generator retains at
    most one storage chunk plus compressed output waiting to be yielded.
    """

    class _StreamingZipSink:
        def __init__(self) -> None:
            self._pending = bytearray()
            self._position = 0

        def write(self, data: bytes) -> int:
            self._pending.extend(data)
            self._position += len(data)
            return len(data)

        def tell(self) -> int:
            return self._position

        def seek(self, *_args: Any) -> int:
            raise OSError("streaming ZIP sink is not seekable")

        def flush(self) -> None:
            pass

        def pop(self) -> bytes:
            if not self._pending:
                return b""
            data = bytes(self._pending)
            self._pending.clear()
            return data

    sink = _StreamingZipSink()
    zf = zipfile.ZipFile(
        sink,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    )  # type: ignore[call-overload]
    try:
        for arcname, file_key in entries:
            entry_started = False
            try:
                async with stream_object(file_key) as body:
                    # Probe storage before writing the ZIP entry header. If the
                    # object cannot be opened/read at all, it can still be omitted
                    # without corrupting bytes already sent for the archive.
                    chunk = await body.read(65536)
                    with zf.open(arcname, mode="w", force_zip64=True) as member:
                        entry_started = True
                        while chunk:
                            member.write(chunk)
                            pending = sink.pop()
                            if pending:
                                yield pending
                            # Backpressure is preserved: the next storage read
                            # happens only after the consumer resumes the generator.
                            chunk = await body.read(65536)

                    pending = sink.pop()
                    if pending:
                        yield pending
            except Exception as exc:
                logger.warning(
                    "Failed to stream ZIP entry for key %s: %s",
                    file_key,
                    exc,
                    exc_info=True,
                )
                if entry_started:
                    # Returning a valid ZIP containing a silently truncated file
                    # would be worse than an interrupted download.
                    raise
                continue

        zf.close()
        final = sink.pop()
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
    if redis is None:
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
    if redis is None:
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
    current_user: ReadUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis | None, Depends(get_redis)] = None,  # type: ignore[type-arg]
) -> StreamingResponse:
    """Stream a directory ZIP for bearer API clients or cookie-authenticated browsers."""
    if redis is None:
        redis = redis_client

    await rate_limit_downloads(request, current_user, db, redis)

    try:
        dir_name, entries = await directory_service.get_directory_download_entries(db, id)
    except ValueError as exc:
        raise BadRequestError(str(exc))

    if not entries:
        raise BadRequestError("This directory contains no downloadable files.")

    safe_name = (
        dir_name.encode("ascii", "replace").decode("ascii").replace("/", "_") or "directory"
    )
    encoded_name = quote(dir_name)
    disposition = (
        f'attachment; filename="{safe_name}.zip"; filename*=UTF-8\'\'{encoded_name}.zip'
    )

    return StreamingResponse(
        _generate_zip(entries),
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )


class IconUpdateBody(BaseModel):
    icon: str | None


class ColorUpdateBody(BaseModel):
    color: str | None


@router.patch("/{id}/icon")
async def set_directory_icon(
    id: uuid.UUID,
    body: IconUpdateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    if not current_user.is_staff:
        raise ForbiddenError("Only staff can update directory icons")
    await directory_service.update_directory_icon(db, id, body.icon)
    broadcast_to_topic(str(id), {"type": "directory_icon_updated", "icon": body.icon})
    return {"ok": True}


@router.patch("/{id}/color")
async def set_directory_color(
    id: uuid.UUID,
    body: ColorUpdateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    if not current_user.is_staff:
        raise ForbiddenError("Only staff can update directory colors")
    await directory_service.update_directory_color(db, id, body.color)
    broadcast_to_topic(str(id), {"type": "directory_color_updated", "color": body.color})
    return {"ok": True}


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
