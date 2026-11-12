import json
import logging
import uuid
import zipfile
from collections.abc import AsyncGenerator
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import (
    BadRequestError,
    ForbiddenError,
)
from app.core.database.database import get_db
from app.core.database.redis import RedisClient, get_redis, redis_client
from app.core.events.limiter import limiter
from app.core.events.sse import broadcast_to_topic
from app.core.storage.facade import stream_object
from app.dependencies.auth import ReadUser, get_current_user
from app.dependencies.rate_limit import check_download_budget, rate_limit_downloads
from app.models.material import Material
from app.models.user import User
from app.schemas.directory import DirectoryBreadcrumb
from app.services import directory as directory_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/directories", tags=["directories"])


_MAX_RESOLVE_PATH_IDS = 250


class ResolvePathsRequest(BaseModel):
    directory_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_RESOLVE_PATH_IDS)
    material_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_RESOLVE_PATH_IDS)

    @model_validator(mode="after")
    def validate_combined_cardinality(self) -> "ResolvePathsRequest":
        # This is a work budget, not merely a uniqueness budget. A material can
        # resolve to a directory ID different from every explicit directory ID.
        if len(self.directory_ids) + len(self.material_ids) > _MAX_RESOLVE_PATH_IDS:
            raise ValueError(f"At most {_MAX_RESOLVE_PATH_IDS} IDs may be resolved at once")
        return self


class MaterialResolveOut(BaseModel):
    directory_id: str | None
    title: str


class ResolvePathsResponse(BaseModel):
    directories: dict[uuid.UUID, list[DirectoryBreadcrumb]] = {}
    materials: dict[uuid.UUID, MaterialResolveOut] = {}


@router.post("/resolve-paths", response_model=ResolvePathsResponse)
@limiter.limit("60/minute")
async def resolve_paths(
    request: Request,
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
        unique_dir_ids = set(req.directory_ids)
        # Defense in depth: cap the actual recursive-CTE roots after materials have
        # been translated to directories, not just the incoming JSON cardinality.
        if len(unique_dir_ids) > _MAX_RESOLVE_PATH_IDS:
            raise BadRequestError(
                f"At most {_MAX_RESOLVE_PATH_IDS} directory paths may be resolved"
            )
        dir_paths = await directory_service.get_directory_breadcrumb_paths(db, unique_dir_ids)
        for did, path in dir_paths.items():
            if path:
                resp.directories[did] = [DirectoryBreadcrumb.model_validate(x) for x in path]

    return resp


# GET /{id}, /{id}/children and /{id}/path are served by the browse router.
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


# The check builds the entry list; the download that follows reuses it once.
_ZIP_ENTRIES_CACHE_PREFIX = "cache:zip_entries:"
_ZIP_ENTRIES_CACHE_TTL_SECONDS = 30


def _zip_entries_cache_key(user: User, directory_id: uuid.UUID) -> str:
    return f"{_ZIP_ENTRIES_CACHE_PREFIX}{user.id}:{directory_id}"


async def _download_entries(
    db: AsyncSession, directory_id: uuid.UUID
) -> tuple[str, list[tuple[str, str]]]:
    try:
        dir_name, entries = await directory_service.get_directory_download_entries(db, directory_id)
    except ValueError as exc:
        raise BadRequestError(str(exc))
    if not entries:
        raise BadRequestError("This directory contains no downloadable files.")
    return dir_name, entries


@router.get("/{id}/download/check", status_code=204)
async def check_directory_download(
    id: uuid.UUID,
    request: Request,
    current_user: ReadUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)] = None,
) -> None:
    """Report whether the directory ZIP can be built, without counting a download.

    Browsers start the ZIP by following a link, which would replace the page with a
    JSON error. They call this first so the error can be shown in place.
    """
    if redis is None:
        redis = redis_client

    await check_download_budget(request, current_user, redis)

    cache_key = _zip_entries_cache_key(current_user, id)
    if await redis.exists(cache_key):
        return
    dir_name, entries = await _download_entries(db, id)
    await redis.setex(cache_key, _ZIP_ENTRIES_CACHE_TTL_SECONDS, json.dumps([dir_name, entries]))


@router.get("/{id}/download")
async def download_directory_zip(
    id: uuid.UUID,
    request: Request,
    current_user: ReadUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)] = None,
) -> StreamingResponse:
    """Stream a directory ZIP for bearer API clients or cookie-authenticated browsers."""
    if redis is None:
        redis = redis_client

    await rate_limit_downloads(request, current_user, db, redis)

    cached = await redis.getdel(_zip_entries_cache_key(current_user, id))
    if cached:
        dir_name, raw_entries = json.loads(cached)
        entries = [(arcname, file_key) for arcname, file_key in raw_entries]
    else:
        dir_name, entries = await _download_entries(db, id)

    safe_name = dir_name.encode("ascii", "replace").decode("ascii").replace("/", "_") or "directory"
    encoded_name = quote(dir_name)
    disposition = f"attachment; filename=\"{safe_name}.zip\"; filename*=UTF-8''{encoded_name}.zip"

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


@router.put("/{id}/favourite")
async def favourite_directory(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, bool]:
    await directory_service.set_directory_favourite(db, user.id, id, favourited=True)
    await db.commit()
    return {"favourited": True}


@router.delete("/{id}/favourite")
async def unfavourite_directory(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, bool]:
    await directory_service.set_directory_favourite(db, user.id, id, favourited=False)
    await db.commit()
    return {"favourited": False}
