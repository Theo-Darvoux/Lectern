"""Backend-agnostic object-storage contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Literal, Protocol, runtime_checkable

ChecksumMode = Literal["when_supported", "when_required"]


@dataclass(frozen=True)
class BackendQuirks:
    """Per-backend deviations from plain S3 behaviour."""

    request_checksum_calculation: ChecksumMode = "when_required"
    response_checksum_validation: ChecksumMode = "when_required"
    presign_put_unsupported_on_custom_domain: bool = False
    strip_bucket_prefix_on_custom_domain: bool = False


@runtime_checkable
class ObjectStorage(Protocol):
    """Structural contract implemented by S3Backend and its subclasses."""

    name: str
    quirks: BackendQuirks

    async def init_s3_client(self) -> None: ...
    async def close_s3_client(self) -> None: ...
    def get_s3_client(
        self, cfg: dict[str, Any] | None = None
    ) -> AbstractAsyncContextManager[Any]: ...

    async def upload_file(
        self,
        file_obj: bytes | IO[bytes] | Any,
        file_key: str,
        content_type: str | None = None,
        content_encoding: str | None = None,
        content_disposition: str | None = "attachment",
    ) -> None: ...
    async def upload_file_multipart(
        self,
        file_path: Path,
        file_key: str,
        content_type: str = "application/octet-stream",
        content_encoding: str | None = None,
        content_disposition: str | None = "attachment",
        chunk_size: int | None = ...,
    ) -> None: ...
    async def create_multipart_upload(
        self,
        file_key: str,
        content_type: str = "application/octet-stream",
        content_encoding: str | None = None,
        content_disposition: str | None = "attachment",
    ) -> str: ...
    async def upload_part(
        self, file_key: str, s3_upload_id: str, part_number: int, body: bytes
    ) -> str: ...
    async def complete_multipart_upload(
        self, file_key: str, s3_upload_id: str, parts: list[dict[str, int | str]]
    ) -> None: ...
    async def abort_multipart_upload(self, file_key: str, s3_upload_id: str) -> None: ...
    async def generate_presigned_upload_part(
        self, file_key: str, s3_upload_id: str, part_number: int, ttl: int = 3600
    ) -> str: ...

    async def download_file(
        self,
        file_key: str,
        dest_path: str | Path,
        *,
        decompress: bool = False,
        max_bytes: int | None = None,
    ) -> None: ...
    async def download_file_raw(
        self, file_key: str, dest_path: str | Path, *, max_bytes: int | None = None
    ) -> None: ...
    async def get_object_headers(self, file_key: str) -> dict[str, str | None]: ...
    async def download_file_with_hash(
        self,
        file_key: str,
        dest_path: str | Path,
        *,
        max_bytes: int | None = None,
        expected_size: int | None = None,
    ) -> str: ...
    async def read_full_object(self, file_key: str) -> bytes: ...
    async def read_object_bytes(self, file_key: str, byte_count: int = ...) -> bytes: ...
    def stream_object(self, file_key: str) -> AbstractAsyncContextManager[Any]: ...

    async def generate_presigned_put(
        self,
        file_key: str,
        content_type: str,
        ttl: int = 3600,
        content_length: int | None = None,
        checksum_sha256: str | None = None,
    ) -> str: ...
    async def generate_presigned_get(
        self,
        file_key: str,
        ttl: int = 900,
        force_download: bool = True,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str: ...
    async def generate_presigned_get_cached(
        self,
        file_key: str,
        redis: Any,
        ttl: int = 900,
        force_download: bool = True,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str: ...
    async def bust_presign_cache(self, file_key: str, redis: Any) -> None: ...

    async def object_exists(self, file_key: str) -> bool: ...
    async def cas_object_exists(self, sha256: str) -> bool: ...
    async def get_object_info(self, file_key: str) -> dict[str, Any]: ...
    async def update_object_content_type(self, file_key: str, content_type: str) -> None: ...
    async def move_object(self, source_key: str, dest_key: str) -> None: ...
    async def copy_object(self, source_key: str, dest_key: str) -> None: ...
    async def delete_object(self, file_key: str) -> None: ...

    def list_objects(self, prefix: str = "") -> AsyncIterator[dict[str, Any]]: ...
    def list_multipart_uploads(self, prefix: str = "") -> AsyncIterator[dict[str, Any]]: ...

    async def get_public_url(self, file_key: str) -> str: ...
