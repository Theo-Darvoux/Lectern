"""Object-storage facade."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from app.config import settings
from app.core.common.constants import MAGIC_HEADER_SIZE
from app.core.security.async_utils import settle_awaitable

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client

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
    "abort_multipart_upload",
    "bust_presign_cache",
    "cas_object_exists",
    "close_s3_client",
    "complete_multipart_upload",
    "copy_object",
    "create_multipart_upload",
    "delete_object",
    "download_file",
    "download_file_raw",
    "download_file_with_hash",
    "dynamic_part_size",
    "generate_presigned_get",
    "generate_presigned_get_cached",
    "generate_presigned_put",
    "generate_presigned_upload_part",
    "get_delivery",
    "get_object_headers",
    "get_object_info",
    "get_public_url",
    "get_s3_client",
    "get_storage",
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


async def init_s3_client() -> None:
    await get_storage().init_s3_client()


async def close_s3_client() -> None:
    await get_storage().close_s3_client()


def get_s3_client(cfg: dict[str, Any] | None = None) -> AbstractAsyncContextManager[S3Client]:
    return get_storage().get_s3_client(cfg)


def _get_s3_settings() -> dict[str, Any]:
    return get_storage()._settings()


def _is_cas_key(file_key: str) -> bool:
    return file_key.startswith("cas/")


async def _cas_object_size(storage: S3Backend, file_key: str) -> int | None:
    if not await storage.object_exists(file_key):
        return None
    info = await storage.get_object_info(file_key)
    try:
        size = int(info["size"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid object size metadata for {file_key!r}") from exc
    if size < 0:
        raise RuntimeError(f"Invalid negative object size for {file_key!r}")
    return size


async def _bounded_cas_io(writer: Callable[[], Awaitable[None]]) -> None:
    """Bound one live physical CAS request before it can become recoverable."""
    async with asyncio.timeout(settings.cas_mutation_io_timeout_seconds):
        await writer()


async def _accounted_cas_write(file_key: str, writer: Callable[[], Awaitable[None]]) -> None:
    """Journal one CAS write and publish only its real physical byte delta."""
    from app.core.database.redis import redis_client
    from app.core.storage.capacity import (
        abort_cas_storage_mutation,
        cas_storage_mutation,
        commit_cas_storage_delta,
        dispatch_cas_storage_mutation,
        resolve_cas_storage_mutation_by_scan,
    )

    storage = get_storage()
    async with cas_storage_mutation(redis_client, file_key, "write") as mutation:
        mutation_id, mutation_epoch = mutation
        try:
            old_size = await _cas_object_size(storage, file_key) or 0
        except BaseException:
            # No object-store mutation has been dispatched yet.
            await abort_cas_storage_mutation(
                redis_client, mutation_id, mutation_epoch, expected_phase="preflight"
            )
            raise

        await dispatch_cas_storage_mutation(redis_client, mutation_id, mutation_epoch)
        _result, writer_error, caller_cancellation = await settle_awaitable(_bounded_cas_io(writer))
        if writer_error is not None:
            # The remote result can be ambiguous after transport failure. Keep the
            # durable intent unresolved; no successor may certify a clean snapshot.
            raise writer_error

        try:
            new_size = await _cas_object_size(storage, file_key)
        except BaseException:
            # The write settled successfully but HEAD failed. An exact scan while
            # the intent remains unresolved repairs the aggregate without guessing.
            await resolve_cas_storage_mutation_by_scan(redis_client, mutation_id, mutation_epoch)
            raise

        if new_size is None:
            # Do not clear the journal on read-after-write ambiguity.
            raise RuntimeError(f"CAS write did not produce visible object {file_key!r}")

        await commit_cas_storage_delta(
            redis_client,
            new_size - old_size,
            mutation_id,
            mutation_epoch,
        )
        if caller_cancellation is not None:
            raise caller_cancellation


async def _accounted_cas_delete(file_key: str, deleter: Callable[[], Awaitable[None]]) -> None:
    """Journal one CAS delete and publish the observed physical byte delta."""
    from app.core.database.redis import redis_client
    from app.core.storage.capacity import (
        abort_cas_storage_mutation,
        cas_storage_mutation,
        commit_cas_storage_delta,
        dispatch_cas_storage_mutation,
        resolve_cas_storage_mutation_by_scan,
    )

    storage = get_storage()
    async with cas_storage_mutation(redis_client, file_key, "delete") as mutation:
        mutation_id, mutation_epoch = mutation
        try:
            old_size = await _cas_object_size(storage, file_key) or 0
        except BaseException:
            await abort_cas_storage_mutation(
                redis_client, mutation_id, mutation_epoch, expected_phase="preflight"
            )
            raise

        await dispatch_cas_storage_mutation(redis_client, mutation_id, mutation_epoch)
        _result, delete_error, caller_cancellation = await settle_awaitable(
            _bounded_cas_io(deleter)
        )
        if delete_error is not None:
            raise delete_error

        try:
            new_size_or_none = await _cas_object_size(storage, file_key)
        except BaseException:
            await resolve_cas_storage_mutation_by_scan(redis_client, mutation_id, mutation_epoch)
            raise

        if new_size_or_none is not None:
            # A successful delete that is not yet observable as absent is still
            # externally unresolved; do not clear the durable intent.
            raise RuntimeError(f"CAS delete did not remove visible object {file_key!r}")
        new_size = 0

        await commit_cas_storage_delta(
            redis_client,
            new_size - old_size,
            mutation_id,
            mutation_epoch,
        )
        if caller_cancellation is not None:
            raise caller_cancellation


async def _accounted_cas_complex_mutation(
    source_key: str,
    dest_key: str,
    writer: Callable[[], Awaitable[None]],
) -> None:
    """Journal a move touching CAS and account the exact before/after byte delta."""
    from app.core.database.redis import redis_client
    from app.core.storage.capacity import (
        abort_cas_storage_mutation,
        cas_storage_mutation,
        commit_cas_storage_delta,
        dispatch_cas_storage_mutation,
        resolve_cas_storage_mutation_by_scan,
    )

    storage = get_storage()
    tracked_keys = tuple(key for key in dict.fromkeys((source_key, dest_key)) if _is_cas_key(key))
    journal_key = dest_key if _is_cas_key(dest_key) else source_key

    async with cas_storage_mutation(redis_client, journal_key, "move") as mutation:
        mutation_id, mutation_epoch = mutation
        try:
            old_size = 0
            for key in tracked_keys:
                old_size += await _cas_object_size(storage, key) or 0
        except BaseException:
            await abort_cas_storage_mutation(
                redis_client, mutation_id, mutation_epoch, expected_phase="preflight"
            )
            raise

        await dispatch_cas_storage_mutation(redis_client, mutation_id, mutation_epoch)
        _result, move_error, caller_cancellation = await settle_awaitable(_bounded_cas_io(writer))
        if move_error is not None:
            raise move_error

        try:
            after_sizes = {key: await _cas_object_size(storage, key) for key in tracked_keys}
        except BaseException:
            await resolve_cas_storage_mutation_by_scan(redis_client, mutation_id, mutation_epoch)
            raise

        if _is_cas_key(dest_key) and after_sizes.get(dest_key) is None:
            raise RuntimeError(f"CAS move destination is not visible: {dest_key!r}")
        if (
            _is_cas_key(source_key)
            and source_key != dest_key
            and after_sizes.get(source_key) is not None
        ):
            raise RuntimeError(f"CAS move source is still visible: {source_key!r}")

        new_size = sum(size or 0 for size in after_sizes.values())
        await commit_cas_storage_delta(
            redis_client,
            new_size - old_size,
            mutation_id,
            mutation_epoch,
        )
        if caller_cancellation is not None:
            raise caller_cancellation


async def upload_file(
    file_obj: bytes | IO[bytes] | Any,
    file_key: str,
    content_type: str | None = None,
    content_encoding: str | None = None,
    content_disposition: str | None = "attachment",
) -> None:
    storage = get_storage()

    async def _write() -> None:
        await storage.upload_file(
            file_obj,
            file_key,
            content_type=content_type,
            content_encoding=content_encoding,
            content_disposition=content_disposition,
        )

    if _is_cas_key(file_key):
        await _accounted_cas_write(file_key, _write)
    else:
        await _write()


async def upload_file_multipart(
    file_path: Path,
    file_key: str,
    content_type: str = "application/octet-stream",
    content_encoding: str | None = None,
    content_disposition: str | None = "attachment",
    chunk_size: int | None = None,
) -> None:
    storage = get_storage()

    async def _write() -> None:
        await storage.upload_file_multipart(
            file_path,
            file_key,
            content_type=content_type,
            content_encoding=content_encoding,
            content_disposition=content_disposition,
            chunk_size=chunk_size,
        )

    if _is_cas_key(file_key):
        await _accounted_cas_write(file_key, _write)
    else:
        await _write()


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


async def upload_part(
    file_key: str,
    s3_upload_id: str,
    part_number: int,
    body: bytes | IO[bytes] | Any,
) -> str:
    return await get_storage().upload_part(file_key, s3_upload_id, part_number, body)


async def complete_multipart_upload(
    file_key: str, s3_upload_id: str, parts: list[dict[str, int | str]]
) -> None:
    storage = get_storage()

    async def _complete() -> None:
        await storage.complete_multipart_upload(file_key, s3_upload_id, parts)

    if _is_cas_key(file_key):
        await _accounted_cas_write(file_key, _complete)
    else:
        await _complete()


async def abort_multipart_upload(file_key: str, s3_upload_id: str) -> None:
    await get_storage().abort_multipart_upload(file_key, s3_upload_id)


async def generate_presigned_upload_part(
    file_key: str,
    s3_upload_id: str,
    part_number: int,
    ttl: int = 3600,
    content_length: int | None = None,
) -> str:
    return await get_storage().generate_presigned_upload_part(
        file_key,
        s3_upload_id,
        part_number,
        ttl=ttl,
        content_length=content_length,
    )


async def download_file(
    file_key: str,
    dest_path: str | Path,
    *,
    decompress: bool = False,
    max_bytes: int | None = None,
) -> None:
    await get_storage().download_file(
        file_key, dest_path, decompress=decompress, max_bytes=max_bytes
    )


async def download_file_raw(
    file_key: str, dest_path: str | Path, *, max_bytes: int | None = None
) -> None:
    """Download raw S3 bytes without gzip decompression or any other post-processing."""
    await get_storage().download_file_raw(file_key, dest_path, max_bytes=max_bytes)


async def get_object_headers(file_key: str) -> dict[str, str | None]:
    """Return ContentType / ContentEncoding / ContentDisposition / CacheControl for a key."""
    return await get_storage().get_object_headers(file_key)


async def download_file_with_hash(
    file_key: str,
    dest_path: str | Path,
    *,
    max_bytes: int | None = None,
    expected_size: int | None = None,
) -> str:
    return await get_storage().download_file_with_hash(
        file_key, dest_path, max_bytes=max_bytes, expected_size=expected_size
    )


async def read_full_object(file_key: str) -> bytes:
    return await get_storage().read_full_object(file_key)


async def read_object_bytes(file_key: str, byte_count: int = MAGIC_HEADER_SIZE) -> bytes:
    return await get_storage().read_object_bytes(file_key, byte_count)


def stream_object(file_key: str) -> AbstractAsyncContextManager[Any]:
    return get_storage().stream_object(file_key)


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


async def object_exists(file_key: str) -> bool:
    return await get_storage().object_exists(file_key)


async def cas_object_exists(sha256: str) -> bool:
    return await get_storage().cas_object_exists(sha256)


async def get_object_info(file_key: str) -> dict[str, Any]:
    return await get_storage().get_object_info(file_key)


async def update_object_content_type(file_key: str, content_type: str) -> None:
    await get_storage().update_object_content_type(file_key, content_type)


async def move_object(source_key: str, dest_key: str) -> None:
    storage = get_storage()

    async def _move() -> None:
        await storage.move_object(source_key, dest_key)

    if _is_cas_key(source_key) or _is_cas_key(dest_key):
        await _accounted_cas_complex_mutation(source_key, dest_key, _move)
    else:
        await _move()


async def copy_object(source_key: str, dest_key: str) -> None:
    storage = get_storage()

    async def _copy() -> None:
        await storage.copy_object(source_key, dest_key)

    if _is_cas_key(dest_key):
        await _accounted_cas_write(dest_key, _copy)
    else:
        await _copy()


async def delete_object(file_key: str) -> None:
    storage = get_storage()

    async def _delete() -> None:
        await storage.delete_object(file_key)

    if _is_cas_key(file_key):
        await _accounted_cas_delete(file_key, _delete)
    else:
        await _delete()


def list_multipart_uploads(prefix: str = "") -> AsyncIterator[dict[str, Any]]:
    return get_storage().list_multipart_uploads(prefix)


def list_objects(prefix: str = "") -> AsyncIterator[dict[str, Any]]:
    return get_storage().list_objects(prefix)


async def get_public_url(file_key: str) -> str:
    return await get_storage().get_public_url(file_key)
