from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import aioboto3
from botocore.config import Config as BotocoreConfig

from app.config import settings
from app.core.constants import MAGIC_HEADER_SIZE
from app.core.typing_ext import S3Client

_session = aioboto3.Session()

# Force SigV4 for all requests (required by R2 and MinIO >= 2022).
_s3_config = BotocoreConfig(
    signature_version="s3v4",
    s3={"use_accelerate_endpoint": settings.s3_use_accelerate_endpoint},
    # R2 returns incorrect CRC checksums; skip validation to avoid false failures
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)

_s3: S3Client | None = None  # persistent client, set by init_s3_client()


async def _get_s3_settings() -> dict[str, Any]:
    """Return effective S3 settings from Redis (DB) fallback to app settings."""
    from app.core.database import async_session_factory
    from app.core.redis import redis_client
    from app.services.auth import get_full_auth_config

    try:
        async with async_session_factory() as db:
            config = await get_full_auth_config(db, redis_client)
            return {
                "endpoint": config.get("s3_endpoint") or settings.s3_endpoint,
                "access_key": config.get("s3_access_key") or settings.s3_access_key,
                "secret_key": config.get("s3_secret_key") or settings.s3_secret_key,
                "bucket": config.get("s3_bucket") or settings.s3_bucket,
                "region": config.get("s3_region") or settings.s3_region,
                "use_ssl": config.get("s3_use_ssl")
                if config.get("s3_use_ssl") is not None
                else settings.s3_use_ssl,
                "public_endpoint": config.get("s3_public_endpoint") or settings.s3_public_endpoint,
            }
    except Exception:
        # Final fallback to settings
        return {
            "endpoint": settings.s3_endpoint,
            "access_key": settings.s3_access_key,
            "secret_key": settings.s3_secret_key,
            "bucket": settings.s3_bucket,
            "region": settings.s3_region,
            "use_ssl": settings.s3_use_ssl,
            "public_endpoint": settings.s3_public_endpoint,
        }


async def init_s3_client() -> None:
    global _s3
    cfg = await _get_s3_settings()
    _s3 = await _session.client(  # type: ignore[call-overload]
        "s3",
        endpoint_url=f"{'https' if cfg['use_ssl'] else 'http'}://{cfg['endpoint']}",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name=cfg["region"],
        config=_s3_config,
    ).__aenter__()


async def close_s3_client() -> None:
    global _s3
    if _s3:
        await _s3.__aexit__(None, None, None)
        _s3 = None


@asynccontextmanager
async def get_s3_client(cfg: dict[str, Any] | None = None) -> AsyncGenerator[S3Client, None]:
    """Yield an S3 client.

    Accept an optional pre-fetched ``cfg`` dict to avoid a second Redis round-trip
    when the caller already called ``_get_s3_settings()``.
    """
    if cfg is None:
        cfg = await _get_s3_settings()

    # In development or if using exactly settings, we can reuse the global _s3
    is_default = (
        cfg["endpoint"] == settings.s3_endpoint
        and cfg["access_key"] == settings.s3_access_key
        and cfg["secret_key"] == settings.s3_secret_key
        and cfg["use_ssl"] == settings.s3_use_ssl
    )

    if is_default and _s3:
        yield _s3
        return

    async with _session.client(  # type: ignore[call-overload]
        "s3",
        endpoint_url=f"{'https' if cfg['use_ssl'] else 'http'}://{cfg['endpoint']}",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name=cfg["region"],
        config=_s3_config,
    ) as client:
        yield client


async def _rewrite_host(url: str, is_put: bool = False, cfg: dict[str, Any] | None = None) -> str:
    """Rewrite host for local development. In production, we avoid rewriting S3 endpoint to Custom Domains for PUT, as R2 Custom Domains do not support presigned PUT requests."""
    if cfg is None:
        cfg = await _get_s3_settings()
    public_endpoint = cfg["public_endpoint"]
    bucket = cfg["bucket"]

    if not public_endpoint:
        return url

    # Cloudflare R2 custom domains do not support presigned PUTs.
    # Therefore, we strictly don't rewrite if this is a production setup (public endpoint not containing "localhost") and it's a PUT request.
    if is_put and "localhost" not in public_endpoint:
        return url

    parsed = urlparse(url)
    scheme = "https" if "localhost" not in public_endpoint else "http"

    # If the user absolutely wants to use the custom domain for GETs, we must ensure the bucket name is stripped
    # from the path, because Cloudflare custom domains map directly to the bucket root.
    path = parsed.path
    if "localhost" not in public_endpoint:
        bucket_prefix = f"/{bucket}/"
        if path.startswith(bucket_prefix):
            path = path[len(bucket_prefix) - 1 :]  # Keep the leading slash: /uploads/...

    return urlunparse(parsed._replace(netloc=public_endpoint, scheme=scheme, path=path))


MULTIPART_THRESHOLD = 5 * 1024 * 1024  # 5 MiB — use multipart above this size
_MULTIPART_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB default


def dynamic_part_size(file_size: int) -> int:
    """Return optimal S3 multipart part size for the given file size (4.8).

    Keeps part count manageable for large files without over-splitting small ones.
    """
    if file_size > 500 * 1024 * 1024:  # > 500 MiB → 32 MiB parts (max ~16 parts/GiB)
        return 32 * 1024 * 1024
    if file_size > 100 * 1024 * 1024:  # > 100 MiB → 16 MiB parts
        return 16 * 1024 * 1024
    return 8 * 1024 * 1024  # default


async def create_multipart_upload(
    file_key: str,
    content_type: str = "application/octet-stream",
    content_disposition: str | None = "attachment",
) -> str:
    """Initiate an S3 multipart upload. Returns the UploadId."""
    cfg = await _get_s3_settings()
    params: dict[str, Any] = {
        "Bucket": cfg["bucket"],
        "Key": file_key,
        "ContentType": content_type,
    }
    if content_disposition:
        params["ContentDisposition"] = content_disposition
    async with get_s3_client(cfg) as client:
        resp = await client.create_multipart_upload(**params)
        return str(resp["UploadId"])


async def upload_part(
    file_key: str,
    s3_upload_id: str,
    part_number: int,
    body: bytes,
) -> str:
    """Upload one part of a multipart upload. Returns the ETag."""
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        resp = await client.upload_part(  # type: ignore[call-arg]
            Bucket=cfg["bucket"],
            Key=file_key,
            UploadId=s3_upload_id,
            PartNumber=part_number,
            Body=body,
        )
        return str(resp["ETag"])


async def complete_multipart_upload(
    file_key: str,
    s3_upload_id: str,
    parts: list[dict[str, int | str]],
) -> None:
    """Complete a multipart upload. ``parts`` is a list of ``{PartNumber, ETag}`` dicts."""
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        await client.complete_multipart_upload(  # type: ignore[call-arg]
            Bucket=cfg["bucket"],
            Key=file_key,
            UploadId=s3_upload_id,
            MultipartUpload={"Parts": parts},
        )


async def abort_multipart_upload(file_key: str, s3_upload_id: str) -> None:
    """Abort a multipart upload, freeing all uploaded parts."""
    cfg = await _get_s3_settings()
    try:
        async with get_s3_client(cfg) as client:
            await client.abort_multipart_upload(  # type: ignore[call-arg]
                Bucket=cfg["bucket"],
                Key=file_key,
                UploadId=s3_upload_id,
            )
    except Exception:
        pass  # Best-effort cleanup


async def upload_file_multipart(
    file_path: "Path",
    file_key: str,
    content_type: str = "application/octet-stream",
    content_encoding: str | None = None,
    content_disposition: str = "attachment",
    chunk_size: int = _MULTIPART_CHUNK_SIZE,
) -> None:
    """Upload a file from disk using S3 multipart upload.

    For files below ``MULTIPART_THRESHOLD`` this falls back to single ``put_object``
    to avoid the multipart overhead.  Above the threshold, parts are uploaded
    sequentially (minimum S3 part size is 5 MiB).
    """
    import asyncio
    from pathlib import Path as _Path

    path = _Path(file_path) if not hasattr(file_path, "stat") else file_path
    file_size = path.stat().st_size

    if file_size < MULTIPART_THRESHOLD:
        # Small file — single put_object
        with open(path, "rb") as fh:
            await upload_file(
                fh.read(),
                file_key,
                content_type=content_type,
                content_encoding=content_encoding,
                content_disposition=content_disposition,
            )
        return

    # Large file — multipart
    s3_upload_id = await create_multipart_upload(
        file_key, content_type=content_type, content_disposition=content_disposition
    )
    parts: list[dict[str, int | str]] = []

    try:
        part_number = 1
        with open(path, "rb") as fh:
            while True:
                chunk = await asyncio.to_thread(fh.read, chunk_size)
                if not chunk:
                    break
                etag = await upload_part(file_key, s3_upload_id, part_number, chunk)
                parts.append({"PartNumber": part_number, "ETag": etag})
                part_number += 1

        await complete_multipart_upload(file_key, s3_upload_id, parts)
    except Exception:
        await abort_multipart_upload(file_key, s3_upload_id)
        raise


async def upload_file(
    file_obj: bytes | AsyncIterator[bytes],
    file_key: str,
    content_type: str | None = None,
    content_encoding: str | None = None,
    content_disposition: str = "attachment",
) -> None:
    """Upload a file-like object to storage.

    ``content_disposition`` defaults to ``"attachment"`` so browsers never
    render uploaded content inline — they must download it.  Pass
    ``content_disposition=None`` to omit the header (e.g. for internal
    quarantine objects that are never served to end-users).
    """
    extra_args: dict[str, Any] = {}
    if content_type:
        extra_args["ContentType"] = content_type
    if content_encoding:
        extra_args["ContentEncoding"] = content_encoding
    if content_disposition:
        extra_args["ContentDisposition"] = content_disposition

    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        await client.put_object(
            Bucket=cfg["bucket"],
            Key=file_key,
            Body=file_obj,
            **extra_args,
        )


async def download_file(file_key: str, dest_path: str | Path) -> None:
    """Download an object from storage to a local path."""
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
        body: Any = response["Body"]
        try:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = await body.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        finally:
            body.close()


async def download_file_with_hash(file_key: str, dest_path: str | Path) -> str:
    """Download an object from storage to a local path and compute its SHA-256 in one pass."""
    import asyncio
    import hashlib

    hasher = hashlib.sha256()
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
        body: Any = response["Body"]
        try:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = await body.read(64 * 1024)
                    if not chunk:
                        break

                    # Batch disk write and SHA-256 hash in the same thread call
                    # to keep both CPU-bound hashing and I/O off the event loop
                    # (audit review fix).
                    def _write_and_hash(c: bytes = chunk) -> None:
                        f.write(c)
                        hasher.update(c)

                    await asyncio.to_thread(_write_and_hash)
        finally:
            body.close()
    return hasher.hexdigest()


async def generate_presigned_put(
    file_key: str,
    content_type: str,
    ttl: int = 3600,
    content_length: int | None = None,
    checksum_sha256: str | None = None,
) -> str:
    cfg = await _get_s3_settings()
    params: dict[str, Any] = {
        "Bucket": cfg["bucket"],
        "Key": file_key,
        "ContentType": content_type,
    }
    conditions: list[Any] = [
        {"bucket": cfg["bucket"]},
        ["starts-with", "$key", file_key],
        {"content-type": content_type},
    ]
    if content_length is not None:
        # Enforce exact content length via AWS condition
        conditions.append(["content-length-range", content_length, content_length])

    async with get_s3_client(cfg) as client:
        # Note: Boto3 client.generate_presigned_url doesn't cleanly encode strict length constraint conditions
        # for `put_object_url` on some implementations unless using generate_presigned_post,
        # but modern custom S3 backends or AWS accept `ContentLength` in the headers.
        # We inject `Content-Length` into expected params.
        if content_length is not None:
            params["ContentLength"] = content_length
        if checksum_sha256 is not None:
            params["ChecksumAlgorithm"] = "SHA256"
            import base64

            params["ChecksumSHA256"] = base64.b64encode(bytes.fromhex(checksum_sha256)).decode()
        url: str = await client.generate_presigned_url(  # type: ignore[call-arg]
            "put_object",
            Params=params,
            ExpiresIn=ttl,
        )
        return await _rewrite_host(url, is_put=True, cfg=cfg)


async def generate_presigned_get(
    file_key: str,
    ttl: int = 900,
    force_download: bool = True,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """Generate a presigned GET URL for a stored object.

    Args:
        file_key: S3 object key.  Must NOT be a quarantine/ key — those are
            unscanned and must never be served to end-users.
        ttl: URL lifetime in seconds (default 15 min).
        force_download: When True (default) sets ``ResponseContentDisposition``
            to ``attachment`` so browsers download rather than render the file.
            Pass False only for inline viewing (e.g. OnlyOffice integration).
        filename: Override the download filename via ResponseContentDisposition.
            Essential for CAS keys (``cas/{hmac}``) which are opaque hashes.
        content_type: Override the response Content-Type via ResponseContentType.
    """
    if file_key.startswith("quarantine/"):
        raise ValueError(
            f"Refusing to generate presigned GET for unscanned quarantine key: {file_key}"
        )

    cfg = await _get_s3_settings()
    params: dict[str, Any] = {
        "Bucket": cfg["bucket"],
        "Key": file_key,
    }

    if filename:
        from urllib.parse import quote

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

    async with get_s3_client(cfg) as client:
        url: str = await client.generate_presigned_url(  # type: ignore[call-arg]
            "get_object",
            Params=params,
            ExpiresIn=ttl,
        )
        return await _rewrite_host(url, is_put=False, cfg=cfg)


async def object_exists(file_key: str) -> bool:
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        try:
            await client.head_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
            return True
        except client.exceptions.ClientError:
            return False


async def cas_object_exists(sha256: str) -> bool:
    """Check if a file with the given SHA-256 exists in the CAS prefix."""
    from app.core.cas import hmac_cas_key

    # We use the HMAC as the key name in the cas/ prefix
    cas_id = hmac_cas_key(sha256).split(":")[-1]
    return await object_exists(f"cas/{cas_id}")


async def get_object_info(file_key: str) -> dict[str, Any]:
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        response = await client.head_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
        return {
            "size": response["ContentLength"],
            "content_type": response["ContentType"],
        }


async def move_object(source_key: str, dest_key: str) -> None:
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        await client.copy_object(  # type: ignore[call-arg]
            Bucket=cfg["bucket"],
            CopySource={"Bucket": cfg["bucket"], "Key": source_key},
            Key=dest_key,
        )
    await delete_object(source_key)


async def copy_object(source_key: str, dest_key: str) -> None:
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        await client.copy_object(  # type: ignore[call-arg]
            Bucket=cfg["bucket"],
            CopySource={"Bucket": cfg["bucket"], "Key": source_key},
            Key=dest_key,
        )


async def delete_object(file_key: str) -> None:
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        await client.delete_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]

    # Remove from quota sorted set for both staging prefixes.
    # quarantine/ keys are added on upload; uploads/ keys are added after clean processing.
    try:
        if file_key.startswith("uploads/") or file_key.startswith("quarantine/"):
            parts = file_key.split("/")
            if len(parts) >= 3:
                user_id = parts[1]
                from app.core.redis import redis_client

                await redis_client.zrem(f"quota:uploads:{user_id}", file_key)
    except Exception as e:
        import logging

        logging.getLogger("wikint").warning(
            "Failed to remove deleted object %s from Redis quota: %s", file_key, e
        )


_READ_FULL_OBJECT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB safety guard (4.14)


async def read_full_object(file_key: str) -> bytes:
    """Read the entire object from storage into memory.

    Raises ``ValueError`` if the object exceeds 50 MB to prevent OOM errors.
    Use ``download_file_with_hash`` for large objects.
    """
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
        content_length = int(cast(Any, response.get("ContentLength")) or 0)
        if content_length > _READ_FULL_OBJECT_MAX_BYTES:
            raise ValueError(
                f"Object {file_key!r} ({content_length} bytes) exceeds the "
                f"{_READ_FULL_OBJECT_MAX_BYTES // 1024 // 1024} MiB limit for "
                "read_full_object. Use download_file_with_hash for large files."
            )
        body: Any = response["Body"]
        return await body.read()  # type: ignore[no-any-return]


async def read_object_bytes(file_key: str, byte_count: int = MAGIC_HEADER_SIZE) -> bytes:
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        try:
            response = await client.get_object(  # type: ignore[call-arg]
                Bucket=cfg["bucket"], Key=file_key, Range=f"bytes=0-{byte_count - 1}"
            )
            body: Any = response["Body"]
            return await body.read()  # type: ignore[no-any-return]
        except client.exceptions.ClientError:
            return b""


async def update_object_content_type(file_key: str, content_type: str) -> None:
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as client:
        await client.copy_object(  # type: ignore[call-arg]
            Bucket=cfg["bucket"],
            CopySource={"Bucket": cfg["bucket"], "Key": file_key},
            Key=file_key,
            MetadataDirective="REPLACE",
            ContentType=content_type,
        )


@asynccontextmanager
async def stream_object(file_key: str) -> AsyncGenerator[Any, None]:
    """Yield S3 response body for chunked reading via ``await body.read(size)``."""
    cfg = await _get_s3_settings()
    if _s3:
        response = await _s3.get_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
        body: Any = response["Body"]
        try:
            yield body
        finally:
            body.close()
        return

    async with get_s3_client(cfg) as client:
        response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)  # type: ignore[call-arg]
        body: Any = response["Body"]  # type: ignore[no-redef]
        yield body


async def list_multipart_uploads(prefix: str = "") -> AsyncIterator[dict[str, Any]]:
    """Yield all in-progress S3 multipart uploads under the given prefix."""
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as s3:
        paginator = s3.get_paginator("list_multipart_uploads")
        kwargs: dict[str, Any] = {"Bucket": cfg["bucket"]}
        if prefix:
            kwargs["Prefix"] = prefix
        async for page in paginator.paginate(**kwargs):
            uploads: list[dict[str, Any]] = page.get("Uploads", [])
            for mp in uploads:
                yield mp


async def list_objects(prefix: str = "") -> AsyncIterator[dict[str, Any]]:
    """Yield all objects in the bucket, optionally under a prefix."""
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as s3:
        paginator = s3.get_paginator("list_objects_v2")
        kwargs: dict[str, Any] = {"Bucket": cfg["bucket"]}
        if prefix:
            kwargs["Prefix"] = prefix
        async for page in paginator.paginate(**kwargs):
            contents: list[dict[str, Any]] = page.get("Contents", [])
            for obj in contents:
                yield obj


async def generate_presigned_upload_part(
    file_key: str,
    s3_upload_id: str,
    part_number: int,
    ttl: int = 3600,
) -> str:
    """Generate a presigned URL for uploading one part of a multipart upload."""
    cfg = await _get_s3_settings()
    async with get_s3_client(cfg) as s3:
        url = await s3.generate_presigned_url(  # type: ignore[call-arg]
            "upload_part",
            Params={
                "Bucket": cfg["bucket"],
                "Key": file_key,
                "UploadId": s3_upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=ttl,
        )
    return await _rewrite_host(url, cfg=cfg)


generate_presigned_get_url = generate_presigned_get
generate_presigned_put_url = generate_presigned_put
