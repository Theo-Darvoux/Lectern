import base64
import hashlib
import hmac
import json
import time


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

    The Worker verifies the signature and the ``exp`` field before serving the ZIP.
    TTL defaults to 5 minutes — enough for a redirect to complete.
    ``part`` / ``total`` are included when the directory is split across multiple ZIPs
    so the Worker can set the correct ``Content-Disposition`` filename (e.g. "Dir (2).zip").
    """
    payload: dict = {
        "dir_name": dir_name,
        "entries": [{"arcname": a, "r2_key": k} for a, k in entries],
        "exp": int(time.time()) + ttl,
    }
    if part is not None:
        payload["part"] = part
    if total is not None:
        payload["total"] = total
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{payload_b64}.{sig_b64}"


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

    The Worker verifies the signature and uses the payload to set headers (like Content-Disposition).
    """
    payload: dict = {
        "r2_key": r2_key,
        "exp": int(time.time()) + ttl,
        "force_download": force_download,
    }
    if filename:
        payload["filename"] = filename
    if content_type:
        payload["content_type"] = content_type

    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{payload_b64}.{sig_b64}"
