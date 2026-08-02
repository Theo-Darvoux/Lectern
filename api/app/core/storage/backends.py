"""Concrete storage backends."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .base import BackendQuirks
from .s3 import S3Backend


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
        """Replace Content-Type through a raw disk spool and atomic PUT.

        SeaweedFS 4.29 can ignore replacement metadata on both self-copy and
        non-self ``CopyObject`` requests. Spooling the raw object to disk avoids
        buffering attacker-sized files in memory, and the final ``PutObject``
        atomically replaces the key with the requested metadata.
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

        with TemporaryDirectory(prefix="lectern-seaweedfs-metadata-") as temp_dir:
            temp_path = Path(temp_dir) / "object"
            await self.download_file_raw(
                file_key,
                temp_path,
                max_bytes=content_length,
            )
            actual_size = temp_path.stat().st_size
            if actual_size != content_length:
                raise RuntimeError(
                    "SeaweedFS object size changed during content-type update "
                    f"({actual_size} != {content_length})"
                )

            with temp_path.open("rb") as body:
                async with self._client(cfg) as client:
                    await client.put_object(
                        Bucket=cfg["bucket"],
                        Key=file_key,
                        Body=body,
                        ContentLength=content_length,
                        ContentType=content_type,
                        **preserved,
                    )
                    updated = await client.head_object(Bucket=cfg["bucket"], Key=file_key)

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
