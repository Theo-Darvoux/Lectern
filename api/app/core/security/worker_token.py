import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _encode_and_sign_payload(payload: dict[str, Any], secret: str) -> str:
    """Encode payload dict to unpadded base64url JSON and append HMAC-SHA256 signature."""
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def make_zip_token(
    dir_name: str,
    entries: list[tuple[str, str]],
    secret: str,
    ttl: int = 300,
    part: int | None = None,
    total: int | None = None,
) -> str:
    """Return a short-lived HMAC-SHA256 signed token for the Cloudflare Worker ZIP generation.

    Format: ``<base64url(payload_json)>.<base64url(hmac_sha256)>``
    """
    payload: dict[str, Any] = {
        "dir_name": dir_name,
        "entries": [{"arcname": a, "r2_key": k} for a, k in entries],
        "exp": int(time.time()) + ttl,
    }
    if part is not None:
        payload["part"] = part
    if total is not None:
        payload["total"] = total
    return _encode_and_sign_payload(payload, secret)


def make_file_token(
    r2_key: str,
    secret: str,
    ttl: int = 900,
    filename: str | None = None,
    content_type: str | None = None,
    force_download: bool = True,
) -> str:
    """Return a short-lived HMAC-SHA256 signed token for secure edge caching of a single file.

    Format: ``<base64url(payload_json)>.<base64url(hmac_sha256)>``
    """
    payload: dict[str, Any] = {
        "r2_key": r2_key,
        "exp": int(time.time()) + ttl,
        "force_download": force_download,
    }
    if filename:
        payload["filename"] = filename
    if content_type:
        payload["content_type"] = content_type

    return _encode_and_sign_payload(payload, secret)
