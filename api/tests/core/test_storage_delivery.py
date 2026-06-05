"""Tests for the storage delivery seam (app.core.storage.delivery).

The delivery strategy decides whether a stored object is served via an
HMAC-signed worker (Cloudflare or the self-hosted Node port — same token
contract) or via a presigned S3 GET. Switching is config-only: setting
``worker_zip_url`` activates ``WorkerDelivery``; clearing it falls back to
``DirectDelivery`` (presigned S3).
"""

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import settings
from app.core.storage import generate_presigned_get, get_public_url
from app.core.storage.delivery import DirectDelivery, WorkerDelivery, get_delivery

SECRET = "delivery-test-secret"


def _verify_token(token: str, secret: str) -> dict:
    payload_b64, sig_b64 = token.split(".")
    expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    assert sig_b64 == expected_b64, "signature mismatch"
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def test_get_delivery_direct_when_no_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "worker_zip_url", "")
    assert isinstance(get_delivery(), DirectDelivery)


def test_get_delivery_worker_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "worker_zip_url", "https://cdn.example.com")
    monkeypatch.setattr(settings, "worker_zip_hmac_secret", SECRET)
    delivery = get_delivery()
    assert isinstance(delivery, WorkerDelivery)
    assert delivery.base_url == "https://cdn.example.com"


def test_direct_delivery_returns_none() -> None:
    d = DirectDelivery()
    assert (
        d.file_url("k", ttl=60, force_download=True, filename=None, content_type=None)
        is None
    )
    assert d.public_url("branding/logo.webp") is None


def test_worker_delivery_file_url_signs_token() -> None:
    d = WorkerDelivery("https://cdn.example.com/", SECRET)
    url = d.file_url(
        "uploads/u/1/Final Report.pdf",
        ttl=900,
        force_download=False,
        filename="Final Report.pdf",
        content_type="application/pdf",
    )
    assert url is not None
    parsed = urlparse(url)
    assert parsed.path == "/file/uploads/u/1/Final%20Report.pdf"
    token = parse_qs(parsed.query)["token"][0]
    payload = _verify_token(token, SECRET)
    assert payload["r2_key"] == "uploads/u/1/Final Report.pdf"
    assert payload["force_download"] is False
    assert payload["content_type"] == "application/pdf"


def test_worker_delivery_file_url_without_secret_falls_back() -> None:
    # No secret → cannot sign → defer to presigned S3 (None).
    d = WorkerDelivery("https://cdn.example.com", "")
    assert (
        d.file_url("k", ttl=60, force_download=True, filename=None, content_type=None)
        is None
    )


def test_worker_delivery_public_url_is_unsigned_root() -> None:
    d = WorkerDelivery("https://cdn.example.com", SECRET)
    assert d.public_url("branding/logo.webp") == "https://cdn.example.com/branding/logo.webp"


@pytest.mark.asyncio
async def test_generate_presigned_get_uses_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a worker configured, the facade returns a signed /file/ URL and never
    touches S3."""
    monkeypatch.setattr(settings, "worker_zip_url", "https://cdn.example.com")
    monkeypatch.setattr(settings, "worker_zip_hmac_secret", SECRET)

    url = await generate_presigned_get("cas/abc123", ttl=120, filename="doc.pdf")
    parsed = urlparse(url)
    assert parsed.netloc == "cdn.example.com"
    assert parsed.path == "/file/cas/abc123"
    payload = _verify_token(parse_qs(parsed.query)["token"][0], SECRET)
    assert payload["r2_key"] == "cas/abc123"


@pytest.mark.asyncio
async def test_get_public_url_uses_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "worker_zip_url", "https://cdn.example.com")
    url = await get_public_url("branding/logo.webp")
    assert url == "https://cdn.example.com/branding/logo.webp"


@pytest.mark.asyncio
async def test_generate_presigned_get_rejects_quarantine() -> None:
    with pytest.raises(ValueError, match="quarantine"):
        await generate_presigned_get("quarantine/u/1/evil.exe")
