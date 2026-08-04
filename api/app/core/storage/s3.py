"""aioboto3 implementation of ObjectStorage."""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import inspect
import logging
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlparse, urlunparse

import aioboto3
from aiobotocore.config import AioConfig

from app.config import settings
from app.core.common.constants import MAGIC_HEADER_SIZE
from app.core.security.async_utils import shielded_await, shielded_to_thread

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client

from .base import BackendQuirks
from .delivery import get_delivery

logger = logging.getLogger(__name__)

MULTIPART_THRESHOLD = 5 * 1024 * 1024  # 5 MiB
_MULTIPART_CONCURRENCY = 4  # max concurrent S3 part uploads

_PRESIGN_CACHE_TTL = 12 * 60  # seconds refresh before the 15-min R2 TTL
_PRESIGN_CACHE_PREFIX = "presign:"

_READ_FULL_OBJECT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB safety


def dynamic_part_size(file_size: int) -> int:
    """Return optimal S3 multipart part size for the given file size (4.8).

    Keeps part count manageable for large files without over-splitting small ones.
    """
    if file_size > 500 * 1024 * 1024:  # > 500 MiB : 32 MiB parts (max ~16 parts/GiB)
        return 32 * 1024 * 1024
    if file_size > 100 * 1024 * 1024:  # > 100 MiB : 16 MiB parts
        return 16 * 1024 * 1024
    return 8 * 1024 * 1024  # 8 MiB parts default


def _decompress_gzip_file(
    file_path: Path | str, max_output_bytes: int = _READ_FULL_OBJECT_MAX_BYTES
) -> None:
    path = Path(file_path)
    temp_path = path.with_suffix(path.suffix + ".decompressed.tmp")
    try:
        with gzip.open(path, "rb") as f_in:
            with open(temp_path, "wb") as f_out:
                written = 0
                while chunk := f_in.read(64 * 1024):
                    written += len(chunk)
                    if written > max_output_bytes:
                        raise ValueError("Gzip content exceeds decompressed size limit")
                    f_out.write(chunk)
        temp_path.replace(path)
    except Exception as e:
        logger.warning("Failed to decompress gzip file %s: %s", path, e)
        temp_path.unlink(missing_ok=True)
        raise


async def _close_response_body(body: Any) -> None:
    close_result = body.close()
    if inspect.isawaitable(close_result):
        await close_result


async def _finish_response_body(
    body: Any,
    *,
    primary_error: BaseException | None,
) -> None:
    """Close an S3 body without abandoning cleanup or masking a primary error."""
    try:
        await shielded_await(
            _close_response_body(body),
            description="S3 response body close",
        )
    except asyncio.CancelledError:
        # shielded_await reports cancellation only after close has completed.
        # Preserve an existing primary error; otherwise propagate cancellation.
        if primary_error is None:
            raise
    except Exception as cleanup_error:
        if primary_error is None:
            raise
        logger.warning(
            "S3 response body cleanup failed after %s: %s",
            type(primary_error).__name__,
            cleanup_error,
        )


class S3Backend:
    """aioboto3-backed object storage."""

    name: str = "s3"
    quirks: BackendQuirks = BackendQuirks()

    def __init__(self) -> None:
        self._session = aioboto3.Session()
        self._s3: S3Client | None = None
        self._s3_config = AioConfig(
            signature_version="s3v4",
            s3={"use_accelerate_endpoint": settings.s3_use_accelerate_endpoint},
            request_checksum_calculation=self.quirks.request_checksum_calculation,
            response_checksum_validation=self.quirks.response_checksum_validation,
        )

    def _settings(self) -> dict[str, Any]:
        """Return S3 settings from environment variables."""
        return {
            "endpoint": settings.s3_endpoint,
            "access_key": settings.s3_access_key,
            "secret_key": settings.s3_secret_key,
            "bucket": settings.s3_bucket,
            "region": settings.s3_region,
            "use_ssl": settings.s3_use_ssl,
            "public_endpoint": settings.s3_public_endpoint,
        }

    def _cfg(self) -> dict[str, Any]:
        return self._settings()

    def _client(self, cfg: dict[str, Any] | None = None) -> Any:
        return self.get_s3_client(cfg)

    async def init_s3_client(self) -> None:
        cfg = self._cfg()
        self._s3 = await self._session.client(
            "s3",
            endpoint_url=f"{'https' if cfg['use_ssl'] else 'http'}://{cfg['endpoint']}",
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret_key"],
            region_name=cfg["region"],
            config=self._s3_config,
        ).__aenter__()

    async def close_s3_client(self) -> None:
        if self._s3:
            await self._s3.__aexit__(None, None, None)
            self._s3 = None

    @asynccontextmanager
    async def get_s3_client(
        self, cfg: dict[str, Any] | None = None
    ) -> AsyncGenerator[S3Client, None]:
        """Yield an S3 client."""
        if cfg is None:
            cfg = self._cfg()

        is_default = (
            cfg["endpoint"] == settings.s3_endpoint
            and cfg["access_key"] == settings.s3_access_key
            and cfg["secret_key"] == settings.s3_secret_key
            and cfg["use_ssl"] == settings.s3_use_ssl
        )

        if is_default and self._s3:
            yield self._s3
            return

        async with self._session.client(
            "s3",
            endpoint_url=f"{'https' if cfg['use_ssl'] else 'http'}://{cfg['endpoint']}",
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret_key"],
            region_name=cfg["region"],
            config=self._s3_config,
        ) as client:
            yield client

    def _rewrite_host(
        self, url: str, is_put: bool = False, cfg: dict[str, Any] | None = None
    ) -> str:
        """Rewrite the presigned URL host to the public endpoint."""
        if cfg is None:
            cfg = self._cfg()
        # SigV4 signs the request host. Replacing R2's signed host with a custom
        # delivery domain produces an invalid signature.
        if self.name == "r2":
            return url
        public_endpoint = cfg["public_endpoint"]
        bucket = cfg["bucket"]

        if not public_endpoint:
            return url

        if (
            is_put
            and "localhost" not in public_endpoint
            and self.quirks.presign_put_unsupported_on_custom_domain
        ):
            return url

        if "://" in public_endpoint:
            public_endpoint = urlparse(public_endpoint).netloc

        parsed = urlparse(url)
        scheme = "http" if "localhost" in public_endpoint else "https"

        path = parsed.path
        if "localhost" not in public_endpoint and self.quirks.strip_bucket_prefix_on_custom_domain:
            bucket_prefix = f"/{bucket}/"
            if path.startswith(bucket_prefix):
                path = path[len(bucket_prefix) - 1 :]

        return urlunparse(parsed._replace(netloc=public_endpoint, scheme=scheme, path=path))

    # multipart

    async def create_multipart_upload(
        self,
        file_key: str,
        content_type: str = "application/octet-stream",
        content_encoding: str | None = None,
        content_disposition: str | None = "attachment",
    ) -> str:
        """Initiate an S3 multipart upload. Returns the UploadId."""
        cfg = self._cfg()
        params: dict[str, Any] = {
            "Bucket": cfg["bucket"],
            "Key": file_key,
            "ContentType": content_type,
            "CacheControl": "public, max-age=86400",
        }
        if content_encoding:
            params["ContentEncoding"] = content_encoding
        if content_disposition:
            params["ContentDisposition"] = content_disposition
        async with self._client(cfg) as client:
            resp = await client.create_multipart_upload(**params)
            return str(resp["UploadId"])

    async def upload_part(
        self,
        file_key: str,
        s3_upload_id: str,
        part_number: int,
        body: bytes | IO[bytes] | Any,
    ) -> str:
        """Upload one part of a multipart upload. Returns the ETag."""
        cfg = self._cfg()
        async with self._client(cfg) as client:
            resp = await client.upload_part(
                Bucket=cfg["bucket"],
                Key=file_key,
                UploadId=s3_upload_id,
                PartNumber=part_number,
                Body=body,
            )
            return str(resp["ETag"])

    async def complete_multipart_upload(
        self,
        file_key: str,
        s3_upload_id: str,
        parts: list[dict[str, int | str]],
    ) -> None:
        """Complete a multipart upload. parts is a list of {PartNumber, ETag} dicts."""
        cfg = self._cfg()
        async with self._client(cfg) as client:
            await client.complete_multipart_upload(
                Bucket=cfg["bucket"],
                Key=file_key,
                UploadId=s3_upload_id,
                MultipartUpload={"Parts": parts},
            )

    async def abort_multipart_upload(self, file_key: str, s3_upload_id: str) -> None:
        """Abort a multipart upload, freeing all uploaded parts."""
        cfg = self._cfg()
        try:
            async with self._client(cfg) as client:
                await client.abort_multipart_upload(
                    Bucket=cfg["bucket"],
                    Key=file_key,
                    UploadId=s3_upload_id,
                )
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchUpload", "NotFound"}:
                return
            raise

    async def upload_file_multipart(
        self,
        file_path: Path,
        file_key: str,
        content_type: str = "application/octet-stream",
        content_encoding: str | None = None,
        content_disposition: str | None = "attachment",
        chunk_size: int | None = None,
    ) -> None:
        """Upload a file from disk using S3 multipart upload."""
        path = Path(file_path) if not hasattr(file_path, "stat") else file_path
        file_size = path.stat().st_size
        effective_chunk_size = chunk_size or dynamic_part_size(file_size)
        if effective_chunk_size < MULTIPART_THRESHOLD:
            raise ValueError("S3 multipart chunks must be at least 5 MiB")

        if file_size < MULTIPART_THRESHOLD:
            with open(path, "rb") as fh:
                await self.upload_file(
                    fh.read(),
                    file_key,
                    content_type=content_type,
                    content_encoding=content_encoding,
                    content_disposition=content_disposition,
                )
            return

        s3_upload_id = await self.create_multipart_upload(
            file_key,
            content_type=content_type,
            content_encoding=content_encoding,
            content_disposition=content_disposition,
        )

        pending: set[asyncio.Task[dict[str, int | str]]] = set()
        results: list[dict[str, int | str]] = []
        try:

            async def _upload_one(pnum: int, data: bytes) -> dict[str, int | str]:
                etag = await self.upload_part(file_key, s3_upload_id, pnum, data)
                return {"PartNumber": pnum, "ETag": etag}

            part_number = 1
            with open(path, "rb") as fh:
                while True:
                    chunk = await shielded_to_thread(
                        fh.read,
                        effective_chunk_size,
                        description="multipart file read",
                    )
                    if not chunk:
                        break
                    pending.add(asyncio.create_task(_upload_one(part_number, chunk)))
                    part_number += 1
                    if len(pending) >= _MULTIPART_CONCURRENCY:
                        done, pending = await asyncio.wait(
                            pending, return_when=asyncio.FIRST_COMPLETED
                        )
                        batch_results = await asyncio.gather(*done, return_exceptions=True)
                        for result in batch_results:
                            if isinstance(result, BaseException):
                                raise result
                            results.append(result)

            if pending:
                results.extend(await asyncio.gather(*pending))
                pending.clear()
            parts: list[dict[str, int | str]] = sorted(results, key=lambda p: int(p["PartNumber"]))
            await self.complete_multipart_upload(file_key, s3_upload_id, parts)
        except BaseException:
            primary_error = sys.exception()
            for task in pending:
                task.cancel()
            if pending:
                try:
                    await shielded_await(
                        asyncio.gather(*pending, return_exceptions=True),
                        description="multipart task cleanup",
                    )
                except asyncio.CancelledError:
                    # Pending tasks have completed; preserve the primary result.
                    pass
                except Exception as cleanup_error:
                    logger.warning(
                        "Multipart task cleanup failed after %s: %s",
                        type(primary_error).__name__ if primary_error else "unknown error",
                        cleanup_error,
                    )
            try:
                await shielded_await(
                    self.abort_multipart_upload(file_key, s3_upload_id),
                    description="multipart upload abort",
                )
            except asyncio.CancelledError:
                # Abort completed before cancellation was re-delivered.
                pass
            except Exception as cleanup_error:
                logger.warning(
                    "Failed to abort multipart upload %s after %s: %s",
                    s3_upload_id,
                    type(primary_error).__name__ if primary_error else "unknown error",
                    cleanup_error,
                )
            raise

    async def generate_presigned_upload_part(
        self,
        file_key: str,
        s3_upload_id: str,
        part_number: int,
        ttl: int = 3600,
        content_length: int | None = None,
    ) -> str:
        """Generate a presigned URL for uploading one part of a multipart upload."""
        cfg = self._cfg()
        params: dict[str, Any] = {
            "Bucket": cfg["bucket"],
            "Key": file_key,
            "UploadId": s3_upload_id,
            "PartNumber": part_number,
        }
        if content_length is not None:
            if content_length < 1:
                raise ValueError("Multipart part content length must be positive")
            params["ContentLength"] = content_length
        async with self._client(cfg) as s3:
            url = await s3.generate_presigned_url(
                "upload_part",
                Params=params,
                ExpiresIn=ttl,
            )
        return self._rewrite_host(url, cfg=cfg)

    # single-object upload / download

    async def upload_file(
        self,
        file_obj: bytes | IO[bytes] | Any,
        file_key: str,
        content_type: str | None = None,
        content_encoding: str | None = None,
        content_disposition: str | None = "attachment",
    ) -> None:
        """Upload a file-like object to storage."""
        extra_args: dict[str, Any] = {}
        if content_type:
            extra_args["ContentType"] = content_type
        if content_encoding:
            extra_args["ContentEncoding"] = content_encoding
        if content_disposition:
            extra_args["ContentDisposition"] = content_disposition

        extra_args.setdefault("CacheControl", "public, max-age=86400")

        cfg = self._cfg()
        async with self._client(cfg) as client:
            await client.put_object(
                Bucket=cfg["bucket"],
                Key=file_key,
                Body=file_obj,
                **extra_args,
            )

    async def download_file(
        self,
        file_key: str,
        dest_path: str | Path,
        *,
        decompress: bool = False,
        max_bytes: int | None = None,
    ) -> None:
        """Download an object from storage to a local path."""
        cfg = self._cfg()
        async with self._client(cfg) as client:
            response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)
            body: Any = response["Body"]
            try:
                written = 0
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = await body.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if max_bytes is not None and written > max_bytes:
                            raise ValueError(f"Object {file_key!r} exceeds download size limit")
                        await shielded_to_thread(
                            f.write, chunk, description="S3 download file write"
                        )
            finally:
                await _finish_response_body(body, primary_error=sys.exception())

            if decompress and response.get("ContentEncoding") == "gzip":
                await shielded_to_thread(
                    _decompress_gzip_file,
                    dest_path,
                    description="gzip download decompression",
                )

    async def download_file_raw(
        self, file_key: str, dest_path: str | Path, *, max_bytes: int | None = None
    ) -> None:
        """Download an object's raw bytes to a local path without any post-processing."""
        cfg = self._cfg()
        async with self._client(cfg) as client:
            response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)
            body: Any = response["Body"]
            try:
                written = 0
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = await body.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if max_bytes is not None and written > max_bytes:
                            raise ValueError(f"Object {file_key!r} exceeds download size limit")
                        await shielded_to_thread(
                            f.write, chunk, description="S3 download file write"
                        )
            finally:
                await _finish_response_body(body, primary_error=sys.exception())

    async def get_object_headers(self, file_key: str) -> dict[str, str | None]:
        """Return the HTTP metadata headers stored on an S3 object."""
        cfg = self._cfg()
        async with self._client(cfg) as client:
            response = await client.head_object(Bucket=cfg["bucket"], Key=file_key)
            return {
                "content_type": response.get("ContentType"),
                "content_encoding": response.get("ContentEncoding"),
                "content_disposition": response.get("ContentDisposition"),
                "cache_control": response.get("CacheControl"),
            }

    async def download_file_with_hash(
        self,
        file_key: str,
        dest_path: str | Path,
        *,
        max_bytes: int | None = None,
        expected_size: int | None = None,
    ) -> str:
        """Download an object to a local path and compute its SHA-256 in one pass."""
        hasher = hashlib.sha256()
        cfg = self._cfg()
        async with self._client(cfg) as client:
            response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)
            body: Any = response["Body"]
            try:
                written = 0
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = await body.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if max_bytes is not None and written > max_bytes:
                            raise ValueError(f"Object {file_key!r} exceeds download size limit")

                        def _write_and_hash(c: bytes = chunk) -> None:
                            f.write(c)
                            hasher.update(c)

                        await shielded_to_thread(
                            _write_and_hash, description="S3 download write and hash"
                        )
            finally:
                await _finish_response_body(body, primary_error=sys.exception())
        if expected_size is not None and written != expected_size:
            raise ValueError(
                f"Object {file_key!r} size changed during download ({written} != {expected_size})"
            )
        return hasher.hexdigest()

    async def read_full_object(self, file_key: str) -> bytes:
        """Read the entire object from storage into memory.

        Raises ValueError if the object exceeds 50 MB to prevent OOM errors.
        """
        cfg = self._cfg()
        async with self._client(cfg) as client:
            response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)
            body: Any = response["Body"]
            content_length = int(cast(Any, response.get("ContentLength")) or 0)
            try:
                if content_length > _READ_FULL_OBJECT_MAX_BYTES:
                    raise ValueError(
                        f"Object {file_key!r} ({content_length} bytes) exceeds the "
                        f"{_READ_FULL_OBJECT_MAX_BYTES // 1024 // 1024} MiB limit for "
                        "read_full_object. Use download_file_with_hash for large files."
                    )
                chunks: list[bytes] = []
                total = 0
                while True:
                    if content_length and total >= content_length:
                        break
                    remaining_budget = _READ_FULL_OBJECT_MAX_BYTES - total
                    read_size = min(64 * 1024, remaining_budget + 1)
                    if content_length:
                        read_size = min(read_size, content_length - total)
                    chunk = cast(bytes, await body.read(read_size))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _READ_FULL_OBJECT_MAX_BYTES:
                        raise ValueError(f"Object {file_key!r} exceeds the read_full_object limit")
                    chunks.append(chunk)
                return b"".join(chunks)
            finally:
                await _finish_response_body(body, primary_error=sys.exception())

    async def read_object_bytes(self, file_key: str, byte_count: int = MAGIC_HEADER_SIZE) -> bytes:
        if byte_count <= 0:
            return b""

        cfg = self._cfg()
        async with self._client(cfg) as client:
            try:
                response = await client.get_object(
                    Bucket=cfg["bucket"], Key=file_key, Range=f"bytes=0-{byte_count - 1}"
                )
                body: Any = response["Body"]
                try:
                    return cast(bytes, await body.read(byte_count))[:byte_count]
                finally:
                    await _finish_response_body(body, primary_error=sys.exception())
            except client.exceptions.ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("404", "NoSuchKey", "NotFound"):
                    return b""
                if code in ("416", "InvalidRange", "RequestedRangeNotSatisfiable"):
                    try:
                        metadata = await client.head_object(
                            Bucket=cfg["bucket"],
                            Key=file_key,
                        )
                    except client.exceptions.ClientError as head_error:
                        head_code = head_error.response["Error"]["Code"]
                        if head_code in ("404", "NoSuchKey", "NotFound"):
                            return b""
                        raise
                    if int(metadata.get("ContentLength") or 0) == 0:
                        return b""
                raise

    @asynccontextmanager
    async def stream_object(self, file_key: str) -> AsyncGenerator[Any, None]:
        """Yield S3 response body for chunked reading."""
        cfg = self._cfg()
        if self._s3:
            response = await self._s3.get_object(Bucket=cfg["bucket"], Key=file_key)
            s3_body: Any = response["Body"]
            try:
                yield s3_body
            finally:
                await _finish_response_body(s3_body, primary_error=sys.exception())
            return

        async with self._client(cfg) as client:
            response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)
            s3_body = response["Body"]
            try:
                yield s3_body
            finally:
                await _finish_response_body(s3_body, primary_error=sys.exception())

    # presigned URLs

    async def generate_presigned_put(
        self,
        file_key: str,
        content_type: str,
        ttl: int = 3600,
        content_length: int | None = None,
        checksum_sha256: str | None = None,
    ) -> str:
        cfg = self._cfg()
        params: dict[str, Any] = {
            "Bucket": cfg["bucket"],
            "Key": file_key,
            "ContentType": content_type,
        }
        if content_length is not None:
            params["ContentLength"] = content_length

        async with self._client(cfg) as client:
            if checksum_sha256 is not None:
                params["ChecksumAlgorithm"] = "SHA256"
                params["ChecksumSHA256"] = base64.b64encode(bytes.fromhex(checksum_sha256)).decode()
            url: str = await client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=ttl,
            )
            return self._rewrite_host(url, is_put=True, cfg=cfg)

    async def generate_presigned_get(
        self,
        file_key: str,
        ttl: int = 900,
        force_download: bool = True,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        """Generate a presigned GET URL for a stored object."""
        if file_key.startswith("quarantine/"):
            raise ValueError(
                f"Refusing to generate presigned GET for unscanned quarantine key: {file_key}"
            )

        worker_url = get_delivery().file_url(
            file_key,
            ttl=ttl,
            force_download=force_download,
            filename=filename,
            content_type=content_type,
        )
        if worker_url is not None:
            return worker_url

        cfg = self._cfg()
        params: dict[str, Any] = {
            "Bucket": cfg["bucket"],
            "Key": file_key,
        }

        if filename:
            ascii_safe = filename.encode("ascii", "replace").decode()
            utf8_encoded = quote(filename)
            disposition = "attachment" if force_download else "inline"
            params["ResponseContentDisposition"] = (
                f"{disposition}; filename=\"{ascii_safe}\"; filename*=UTF-8''{utf8_encoded}"
            )
        elif force_download:
            params["ResponseContentDisposition"] = "attachment"

        if content_type:
            params["ResponseContentType"] = content_type

        async with self._client(cfg) as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=ttl,
            )
            return self._rewrite_host(url, is_put=False, cfg=cfg)

    @staticmethod
    def _presign_cache_key(
        file_key: str,
        force_download: bool,
        filename: str | None = None,
        content_type: str | None = None,
        ttl: int = 900,
    ) -> str:
        variant = hashlib.sha256(f"{filename or ''}:{content_type or ''}".encode()).hexdigest()[:12]
        return f"{_PRESIGN_CACHE_PREFIX}{file_key}:{int(force_download)}:{ttl}:{variant}"

    async def generate_presigned_get_cached(
        self,
        file_key: str,
        redis: Any,
        ttl: int = 900,
        force_download: bool = True,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        """Like generate_presigned_get but caches the result in Redis."""
        cache_key = self._presign_cache_key(
            file_key, force_download, filename=filename, content_type=content_type, ttl=ttl
        )

        try:
            cached = await redis.get(cache_key)
            if cached:
                return cached.decode() if isinstance(cached, bytes) else cached
        except Exception as e:
            logger.debug("Presigned URL Redis cache read failed for key %s: %s", file_key, e)

        url = await self.generate_presigned_get(
            file_key,
            ttl=ttl,
            force_download=force_download,
            filename=filename,
            content_type=content_type,
        )

        try:
            await redis.set(cache_key, url, ex=min(_PRESIGN_CACHE_TTL, max(1, ttl - 30)))
        except Exception as e:
            logger.debug("Presigned URL Redis cache write failed for key %s: %s", file_key, e)

        return url

    async def bust_presign_cache(self, file_key: str, redis: Any) -> None:
        """Invalidate all cached presigned URLs for a file (e.g. after a new version)."""
        pattern = f"{_PRESIGN_CACHE_PREFIX}{file_key}:*"
        try:
            keys = [key async for key in redis.scan_iter(pattern)]
            if keys:
                await redis.delete(*keys)
        except Exception as e:
            logger.warning("Failed to bust presign cache for key %s: %s", file_key, e)

    # metadata / existence

    async def object_exists(self, file_key: str) -> bool:
        cfg = self._cfg()
        async with self._client(cfg) as client:
            try:
                await client.head_object(Bucket=cfg["bucket"], Key=file_key)
                return True
            except client.exceptions.ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

    async def cas_object_exists(self, sha256: str) -> bool:
        """Check if a file with the given SHA-256 exists in the CAS prefix."""
        from app.core.security.cas import hmac_cas_key

        cas_id = hmac_cas_key(sha256).split(":")[-1]
        return await self.object_exists(f"cas/{cas_id}")

    async def get_object_info(self, file_key: str) -> dict[str, Any]:
        cfg = self._cfg()
        async with self._client(cfg) as client:
            response = await client.head_object(Bucket=cfg["bucket"], Key=file_key)
            return {
                "size": response["ContentLength"],
                "content_type": response["ContentType"],
            }

    async def update_object_content_type(self, file_key: str, content_type: str) -> None:
        cfg = self._cfg()
        async with self._client(cfg) as client:
            existing = await client.head_object(Bucket=cfg["bucket"], Key=file_key)
            preserved = {
                target: existing[source]
                for source, target in (
                    ("CacheControl", "CacheControl"),
                    ("ContentDisposition", "ContentDisposition"),
                    ("ContentEncoding", "ContentEncoding"),
                    ("ContentLanguage", "ContentLanguage"),
                    ("Expires", "Expires"),
                    ("WebsiteRedirectLocation", "WebsiteRedirectLocation"),
                    ("Metadata", "Metadata"),
                )
                if existing.get(source) is not None
            }
            await client.copy_object(
                Bucket=cfg["bucket"],
                CopySource={"Bucket": cfg["bucket"], "Key": file_key},
                Key=file_key,
                MetadataDirective="REPLACE",
                ContentType=content_type,
                **preserved,
            )

    # copy / move / delete

    async def move_object(self, source_key: str, dest_key: str) -> None:
        cfg = self._cfg()
        async with self._client(cfg) as client:
            await client.copy_object(
                Bucket=cfg["bucket"],
                CopySource={"Bucket": cfg["bucket"], "Key": source_key},
                Key=dest_key,
            )
        await self.delete_object(source_key)

    async def copy_object(self, source_key: str, dest_key: str) -> None:
        cfg = self._cfg()
        async with self._client(cfg) as client:
            await client.copy_object(
                Bucket=cfg["bucket"],
                CopySource={"Bucket": cfg["bucket"], "Key": source_key},
                Key=dest_key,
            )

    async def delete_object(self, file_key: str) -> None:
        cfg = self._cfg()
        async with self._client(cfg) as client:
            await client.delete_object(Bucket=cfg["bucket"], Key=file_key)

    # listing

    async def list_multipart_uploads(self, prefix: str = "") -> AsyncIterator[dict[str, Any]]:
        """Yield all in-progress S3 multipart uploads under the given prefix."""
        cfg = self._cfg()
        async with self._client(cfg) as s3:
            paginator = s3.get_paginator("list_multipart_uploads")
            kwargs: dict[str, Any] = {"Bucket": cfg["bucket"]}
            if prefix:
                kwargs["Prefix"] = prefix
            async for page in paginator.paginate(**kwargs):
                uploads: list[dict[str, Any]] = page.get("Uploads", [])
                for mp in uploads:
                    yield mp

    async def list_objects(self, prefix: str = "") -> AsyncIterator[dict[str, Any]]:
        """Yield all objects in the bucket, optionally under a prefix."""
        cfg = self._cfg()
        async with self._client(cfg) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            kwargs: dict[str, Any] = {"Bucket": cfg["bucket"]}
            if prefix:
                kwargs["Prefix"] = prefix
            async for page in paginator.paginate(**kwargs):
                contents: list[dict[str, Any]] = page.get("Contents", [])
                for obj in contents:
                    yield obj

    # public URL

    async def get_public_url(self, file_key: str) -> str:
        """Return the permanent public URL for an object readable without auth."""
        worker_url = get_delivery().public_url(file_key)
        if worker_url is not None:
            return worker_url

        cfg = self._cfg()
        public_endpoint = cfg["public_endpoint"]
        bucket = cfg["bucket"]
        endpoint = cfg["endpoint"]
        use_ssl = cfg["use_ssl"]

        if public_endpoint:
            if "://" in public_endpoint:
                public_endpoint = urlparse(public_endpoint).netloc
            scheme = "http" if "localhost" in public_endpoint else "https"
            if "localhost" in public_endpoint:
                return f"{scheme}://{public_endpoint}/{bucket}/{quote(file_key, safe='/')}"
            return f"{scheme}://{public_endpoint}/{quote(file_key, safe='/')}"

        scheme = "https" if use_ssl else "http"
        return f"{scheme}://{endpoint}/{bucket}/{quote(file_key, safe='/')}"
