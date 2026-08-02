"""Delivery seam : how a stored object reaches the end user."""

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
    """HMAC-signed delivery via a worker (Cloudflare or self-hosted Node port)."""

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
        if not self.secret:
            return None
        from app.core.security.worker_token import make_file_token

        token = make_file_token(
            r2_key=file_key,
            secret=self.secret,
            ttl=ttl,
            filename=filename,
            content_type=content_type,
            force_download=force_download,
        )

        return f"{self.base_url}/file/{quote(file_key)}?token={token}"

    def public_url(self, file_key: str) -> str | None:
        return f"{self.base_url}/{quote(file_key, safe='/')}"


def get_delivery() -> Delivery:
    """Return the delivery strategy selected by current settings."""
    if settings.worker_zip_url:
        return WorkerDelivery(settings.worker_zip_url, settings.worker_zip_hmac_secret)
    return DirectDelivery()
