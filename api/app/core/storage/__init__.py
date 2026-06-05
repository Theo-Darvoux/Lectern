"""Object-storage facade.

Historically ``app.core.storage`` was a flat module of free functions. It is now
a package: the implementation lives in :class:`S3Backend` (``s3.py``) with
per-store quirks in ``backends.py``, selected at runtime by ``settings.storage_backend``.

This module re-exports the original free-function surface so that the ~30 call
sites across ``routers/`` and ``services/`` keep working unchanged — each wrapper
simply delegates to the active backend returned by :func:`get_storage`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.constants import MAGIC_HEADER_SIZE
from app.core.typing_ext import S3Client

from .backends import GarageBackend, R2Backend, RustFSBackend, SeaweedFSBackend
from .base import BackendQuirks, ObjectStorage
from .delivery import Delivery, DirectDelivery, WorkerDelivery, get_delivery
from .s3 import (
    MULTIPART_THRESHOLD,
    S3Backend,
    dynamic_part_size,
)

__all__ = [
    "MULTIPART_THRESHOLD",
    "BackendQuirks",
    "Delivery",
    "DirectDelivery",
    "ObjectStorage",
    "S3Backend",
    "WorkerDelivery",
    "dynamic_part_size",
    "get_delivery",
    "get_storage",
    # facade re-exports follow (see below)
]

_BACKENDS: dict[str, type[S3Backend]] = {
    "r2": R2Backend,
    "seaweedfs": SeaweedFSBackend,
    "garage": GarageBackend,
    "rustfs": RustFSBackend,
}

_storage: S3Backend | None = None


def get_storage() -> S3Backend:
    """Return the process-wide storage backend selected by ``settings.storage_backend``."""
    global _storage
    if _storage is None:
        key = settings.storage_backend.lower()
        try:
            cls = _BACKENDS[key]
        except KeyError:
            raise ValueError(
                f"Unknown storage_backend {settings.storage_backend!r}. "
                f"Expected one of: {', '.join(sorted(_BACKENDS))}."
            ) from None
        _storage = cls()
    return _storage


# ─── lifecycle / low-level (imported directly by some call sites) ────────────


async def init_s3_client() -> None:
    await get_storage().init_s3_client()


async def close_s3_client() -> None:
    await get_storage().close_s3_client()


def get_s3_client(cfg: dict[str, Any] | None = None) -> AbstractAsyncContextManager[S3Client]:
    return get_storage().get_s3_client(cfg)


def _get_s3_settings() -> dict[str, Any]:
    return get_storage()._settings()


# ─── upload / multipart ──────────────────────────────────────────────────────


async def upload_file(
    file_obj: Any,
    file_key: str,
    content_type: str | None = None,
    content_encoding: str | None = None,
    content_disposition: str | None = "attachment",
) -> None:
    await get_storage().upload_file(
        file_obj,
        file_key,
        content_type=content_type,
        content_encoding=content_encoding,
        content_disposition=content_disposition,
    )


async def upload_file_multipart(
    file_path: Path,
    file_key: str,
    content_type: str = "application/octet-stream",
    content_encoding: str | None = None,
    content_disposition: str | None = "attachment",
    chunk_size: int = 8 * 1024 * 1024,
) -> None:
    await get_storage().upload_file_multipart(
        file_path,
        file_key,
        content_type=content_type,
        content_encoding=content_encoding,
        content_disposition=content_disposition,
        chunk_size=chunk_size,
    )


async def create_multipart_upload(
    file_key: str,
    content_type: str = "application/octet-stream",
    content_encoding: str | None = None,
    content_disposition: str | None = "attachment",
) -> str:
    return await get_storage().create_multipart_upload(
        file_key,
        content_type=content_type,
        content_encoding=content_encoding,
        content_disposition=content_disposition,
    )


async def upload_part(file_key: str, s3_upload_id: str, part_number: int, body: bytes) -> str:
    return await get_storage().upload_part(file_key, s3_upload_id, part_number, body)


async def complete_multipart_upload(
    file_key: str, s3_upload_id: str, parts: list[dict[str, int | str]]
) -> None:
    await get_storage().complete_multipart_upload(file_key, s3_upload_id, parts)


async def abort_multipart_upload(file_key: str, s3_upload_id: str) -> None:
    await get_storage().abort_multipart_upload(file_key, s3_upload_id)


async def generate_presigned_upload_part(
    file_key: str, s3_upload_id: str, part_number: int, ttl: int = 3600
) -> str:
    return await get_storage().generate_presigned_upload_part(
        file_key, s3_upload_id, part_number, ttl=ttl
    )


# ─── download / read ─────────────────────────────────────────────────────────


async def download_file(file_key: str, dest_path: str | Path) -> None:
    await get_storage().download_file(file_key, dest_path)


async def download_file_with_hash(file_key: str, dest_path: str | Path) -> str:
    return await get_storage().download_file_with_hash(file_key, dest_path)


async def read_full_object(file_key: str) -> bytes:
    return await get_storage().read_full_object(file_key)


async def read_object_bytes(file_key: str, byte_count: int = MAGIC_HEADER_SIZE) -> bytes:
    return await get_storage().read_object_bytes(file_key, byte_count)


def stream_object(file_key: str) -> AbstractAsyncContextManager[Any]:
    return get_storage().stream_object(file_key)


# ─── presigned URLs ──────────────────────────────────────────────────────────


async def generate_presigned_put(
    file_key: str,
    content_type: str,
    ttl: int = 3600,
    content_length: int | None = None,
    checksum_sha256: str | None = None,
) -> str:
    return await get_storage().generate_presigned_put(
        file_key,
        content_type,
        ttl=ttl,
        content_length=content_length,
        checksum_sha256=checksum_sha256,
    )


async def generate_presigned_get(
    file_key: str,
    ttl: int = 900,
    force_download: bool = True,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    return await get_storage().generate_presigned_get(
        file_key,
        ttl=ttl,
        force_download=force_download,
        filename=filename,
        content_type=content_type,
    )


async def generate_presigned_get_cached(
    file_key: str,
    redis: Any,
    ttl: int = 900,
    force_download: bool = True,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    return await get_storage().generate_presigned_get_cached(
        file_key,
        redis,
        ttl=ttl,
        force_download=force_download,
        filename=filename,
        content_type=content_type,
    )


async def bust_presign_cache(file_key: str, redis: Any) -> None:
    await get_storage().bust_presign_cache(file_key, redis)


# ─── metadata / existence ────────────────────────────────────────────────────


async def object_exists(file_key: str) -> bool:
    return await get_storage().object_exists(file_key)


async def cas_object_exists(sha256: str) -> bool:
    return await get_storage().cas_object_exists(sha256)


async def get_object_info(file_key: str) -> dict[str, Any]:
    return await get_storage().get_object_info(file_key)


async def update_object_content_type(file_key: str, content_type: str) -> None:
    await get_storage().update_object_content_type(file_key, content_type)


# ─── copy / move / delete ────────────────────────────────────────────────────


async def move_object(source_key: str, dest_key: str) -> None:
    await get_storage().move_object(source_key, dest_key)


async def copy_object(source_key: str, dest_key: str) -> None:
    await get_storage().copy_object(source_key, dest_key)


async def delete_object(file_key: str) -> None:
    await get_storage().delete_object(file_key)


# ─── listing ─────────────────────────────────────────────────────────────────


def list_multipart_uploads(prefix: str = "") -> AsyncIterator[dict[str, Any]]:
    return get_storage().list_multipart_uploads(prefix)


def list_objects(prefix: str = "") -> AsyncIterator[dict[str, Any]]:
    return get_storage().list_objects(prefix)


# ─── public URL ──────────────────────────────────────────────────────────────


async def get_public_url(file_key: str) -> str:
    return await get_storage().get_public_url(file_key)


# ─── back-compat aliases (preserved from the original flat module) ───────────

generate_presigned_get_url = generate_presigned_get
generate_presigned_put_url = generate_presigned_put
generate_presigned_get_url_cached = generate_presigned_get_cached

__all__ += [
    "abort_multipart_upload",
    "bust_presign_cache",
    "cas_object_exists",
    "close_s3_client",
    "complete_multipart_upload",
    "copy_object",
    "create_multipart_upload",
    "delete_object",
    "download_file",
    "download_file_with_hash",
    "generate_presigned_get",
    "generate_presigned_get_cached",
    "generate_presigned_get_url",
    "generate_presigned_get_url_cached",
    "generate_presigned_put",
    "generate_presigned_put_url",
    "generate_presigned_upload_part",
    "get_object_info",
    "get_public_url",
    "get_s3_client",
    "init_s3_client",
    "list_multipart_uploads",
    "list_objects",
    "move_object",
    "object_exists",
    "read_full_object",
    "read_object_bytes",
    "stream_object",
    "update_object_content_type",
    "upload_file",
    "upload_file_multipart",
    "upload_part",
]
