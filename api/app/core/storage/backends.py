"""Concrete storage backends.

Each backend is just :class:`S3Backend` plus a :class:`BackendQuirks` value.
Adding a new S3-compatible store is a matter of declaring its quirks here and
registering it in ``__init__.py`` — no changes to the S3 implementation.
"""

from __future__ import annotations

from .base import BackendQuirks
from .s3 import S3Backend


class R2Backend(S3Backend):
    """Cloudflare R2 — the current production backend.

    R2 needs the checksum workaround (it returns incorrect CRCs) and has two
    custom-domain limitations: it rejects presigned PUTs and maps the bucket to
    the domain root.
    """

    name = "r2"
    quirks = BackendQuirks(
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        presign_put_unsupported_on_custom_domain=True,
        strip_bucket_prefix_on_custom_domain=True,
    )


class SeaweedFSBackend(S3Backend):
    """SeaweedFS — self-hosted S3 store. Behaves like plain S3.

    Presigned PUT works on the public host and the bucket stays in the path, so
    no custom-domain carve-outs are needed. ``when_required`` checksum handling
    is kept as the proven, conservative default (matches the MinIO dev path);
    Phase 0 validation confirms whether SeaweedFS accepts SHA256 checksums.
    """

    name = "seaweedfs"
    quirks = BackendQuirks()


class GarageBackend(S3Backend):
    """Garage — self-hosted, geo-distributed S3 store. Plain S3 semantics."""

    name = "garage"
    quirks = BackendQuirks()


class RustFSBackend(S3Backend):
    """RustFS — self-hosted S3 store. Plain S3 semantics."""

    name = "rustfs"
    quirks = BackendQuirks()
