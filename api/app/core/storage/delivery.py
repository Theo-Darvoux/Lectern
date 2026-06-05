"""Delivery seam — how a stored object reaches the end user.

This is deliberately decoupled from the storage backend (``S3Backend``). Storage
answers "where do the bytes live"; delivery answers "what URL does the browser
hit to fetch them, signed and edge-cached".

Two strategies:

* :class:`WorkerDelivery` — an HMAC-signed worker fronts the bucket and serves
  ``/file/{key}?token=`` (single file, edge-cached) and ``/branding/*`` (public).
  This covers **both** the Cloudflare Worker and the self-hosted Node port from
  ``worker/`` — they share the exact same token contract (``make_file_token``),
  so switching is purely a matter of pointing ``WORKER_ZIP_URL`` at the other
  deployment. No code change.
* :class:`DirectDelivery` — no edge layer; callers fall back to presigned S3
  GETs and direct object URLs.

The active strategy is chosen per call by :func:`get_delivery` from settings, so
runtime config changes are honoured.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from urllib.parse import quote

from app.config import settings


@runtime_checkable
class Delivery(Protocol):
    """How signed/public object URLs are produced for end users."""

    def file_url(
        self,
        file_key: str,
        *,
        ttl: int,
        force_download: bool,
        filename: str | None,
        content_type: str | None,
    ) -> str | None:
        """Signed single-file URL, or ``None`` to fall back to presigned S3."""
        ...

    def public_url(self, file_key: str) -> str | None:
        """Unauthenticated public URL, or ``None`` to fall back to a direct URL."""
        ...


class DirectDelivery:
    """No edge layer. Both methods return ``None`` so storage uses presigned S3."""

    def file_url(
        self,
        file_key: str,
        *,
        ttl: int,
        force_download: bool,
        filename: str | None,
        content_type: str | None,
    ) -> str | None:
        return None

    def public_url(self, file_key: str) -> str | None:
        return None


class WorkerDelivery:
    """HMAC-signed delivery via a worker (Cloudflare or self-hosted Node port).

    ``base_url`` points at whichever worker is deployed; ``secret`` is the shared
    ``WORKER_ZIP_HMAC_SECRET`` used to sign single-file tokens.
    """

    def __init__(self, base_url: str, secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = secret

    def file_url(
        self,
        file_key: str,
        *,
        ttl: int,
        force_download: bool,
        filename: str | None,
        content_type: str | None,
    ) -> str | None:
        # Single-file serving is token-verified; without a secret we cannot sign,
        # so defer to presigned S3 (mirrors the original storage.py behaviour).
        if not self.secret:
            return None
        from app.core.worker_token import make_file_token

        token = make_file_token(
            r2_key=file_key,
            secret=self.secret,
            ttl=ttl,
            filename=filename,
            content_type=content_type,
            force_download=force_download,
        )
        # quote the key so spaces/special chars in the path survive.
        return f"{self.base_url}/file/{quote(file_key)}?token={token}"

    def public_url(self, file_key: str) -> str | None:
        # Branding assets are served unauthenticated from the worker root.
        return f"{self.base_url}/{file_key}"


def get_delivery() -> Delivery:
    """Return the delivery strategy selected by current settings."""
    if settings.worker_zip_url:
        return WorkerDelivery(settings.worker_zip_url, settings.worker_zip_hmac_secret)
    return DirectDelivery()
