"""Concrete storage backends."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from app.core.security.async_utils import shielded_await

from .base import BackendQuirks
from .s3 import (
    MULTIPART_THRESHOLD,
    S3Backend,
    _finish_response_body,
    dynamic_part_size,
)

logger = logging.getLogger(__name__)


class R2Backend(S3Backend):
    """Cloudflare R2"""

    name = "r2"
    quirks = BackendQuirks(
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        presign_put_unsupported_on_custom_domain=True,
        strip_bucket_prefix_on_custom_domain=True,
    )


class SeaweedFSBackend(S3Backend):
    """SeaweedFS, self-hosted S3 store."""

    name = "seaweedfs"
    quirks = BackendQuirks()

    async def update_object_content_type(self, file_key: str, content_type: str) -> None:
        """Atomically rewrite Content-Type without a full-object disk spool.

        SeaweedFS 4.29 can ignore replacement metadata on CopyObject. For a
        non-empty object, stream the current bytes into a same-key multipart
        upload and complete it only after every byte has been read. The original
        object remains authoritative until multipart completion, and any failure
        aborts the replacement upload. Peak application buffering is one S3 part.
        """
        cfg = self._cfg()
        async with self._client(cfg) as client:
            existing = await client.head_object(Bucket=cfg["bucket"], Key=file_key)

        content_length = int(existing.get("ContentLength") or 0)
        preserved: dict[str, Any] = {
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

        # Empty objects have no multipart body to preserve; a zero-byte PUT is
        # below every S3 single-request limit and does not require local spooling.
        if content_length == 0:
            async with self._client(cfg) as client:
                await client.put_object(
                    Bucket=cfg["bucket"],
                    Key=file_key,
                    Body=b"",
                    ContentLength=0,
                    ContentType=content_type,
                    **preserved,
                )
                updated = await client.head_object(Bucket=cfg["bucket"], Key=file_key)
        else:
            # Keep the multipart count within S3's 10,000-part object limit even
            # if future deployments raise today's application size ceilings.
            minimum_for_part_limit = (content_length + 9_999) // 10_000
            part_size = max(
                MULTIPART_THRESHOLD,
                dynamic_part_size(content_length),
                minimum_for_part_limit,
            )
            upload_id: str | None = None
            body: Any | None = None
            try:
                async with self._client(cfg) as client:
                    response = await client.get_object(Bucket=cfg["bucket"], Key=file_key)
                    body = response["Body"]
                    initiated = await client.create_multipart_upload(
                        Bucket=cfg["bucket"],
                        Key=file_key,
                        ContentType=content_type,
                        **preserved,
                    )
                    upload_id = str(initiated["UploadId"])

                    parts: list[dict[str, int | str]] = []
                    total_read = 0
                    part_number = 1
                    while total_read < content_length:
                        target_size = min(part_size, content_length - total_read)
                        chunk = bytearray()
                        while len(chunk) < target_size:
                            piece = await body.read(target_size - len(chunk))
                            if not piece:
                                break
                            chunk.extend(piece)
                        if len(chunk) != target_size:
                            raise RuntimeError(
                                "SeaweedFS object size changed during content-type update "
                                f"({total_read + len(chunk)} != {content_length})"
                            )

                        uploaded = await client.upload_part(
                            Bucket=cfg["bucket"],
                            Key=file_key,
                            UploadId=upload_id,
                            PartNumber=part_number,
                            Body=bytes(chunk),
                        )
                        parts.append({"PartNumber": part_number, "ETag": str(uploaded["ETag"])})
                        total_read += len(chunk)
                        part_number += 1

                    if await body.read(1):
                        raise RuntimeError(
                            "SeaweedFS object grew during content-type update "
                            f"(expected {content_length} bytes)"
                        )
                    await _finish_response_body(body, primary_error=None)
                    body = None

                    await client.complete_multipart_upload(
                        Bucket=cfg["bucket"],
                        Key=file_key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                    )
                    upload_id = None
                    updated = await client.head_object(Bucket=cfg["bucket"], Key=file_key)
            except BaseException:
                primary_error = sys.exception()
                if upload_id is not None:
                    try:
                        await shielded_await(
                            self.abort_multipart_upload(file_key, upload_id),
                            description="SeaweedFS metadata rewrite multipart abort",
                        )
                    except asyncio.CancelledError:
                        # shielded_await completed the abort before redelivering
                        # cancellation; preserve the original cancellation.
                        pass
                    except Exception as cleanup_error:
                        logger.warning(
                            "Failed to abort SeaweedFS metadata rewrite for %s after %s: %s",
                            file_key,
                            type(primary_error).__name__ if primary_error else "unknown error",
                            cleanup_error,
                        )
                raise
            finally:
                if body is not None:
                    await _finish_response_body(body, primary_error=sys.exception())

        if updated.get("ContentType") != content_type:
            raise RuntimeError("SeaweedFS content-type update did not persist")
        if int(updated.get("ContentLength") or 0) != content_length:
            raise RuntimeError("SeaweedFS content-type update changed object size")


class GarageBackend(S3Backend):
    """Garage, self-hosted, geo-distributed S3 store. Plain S3 semantics."""

    name = "garage"
    quirks = BackendQuirks()


class RustFSBackend(S3Backend):
    """RustFS, self-hosted S3 store. Plain S3 semantics."""

    name = "rustfs"
    quirks = BackendQuirks()
