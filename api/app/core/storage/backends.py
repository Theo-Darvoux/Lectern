"""Concrete storage backends."""

from __future__ import annotations

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


class GarageBackend(S3Backend):
    """Garage, self-hosted, geo-distributed S3 store. Plain S3 semantics."""

    name = "garage"
    quirks = BackendQuirks()


class RustFSBackend(S3Backend):
    """RustFS, self-hosted S3 store. Plain S3 semantics."""

    name = "rustfs"
    quirks = BackendQuirks()
