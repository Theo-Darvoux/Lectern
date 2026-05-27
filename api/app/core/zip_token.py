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
) -> str:
    """Return a short-lived HMAC-SHA256 signed token for the Cloudflare Worker.

    Format: ``<base64url(payload_json)>.<base64url(hmac_sha256)>``

    The Worker verifies the signature and the ``exp`` field before serving the ZIP.
    TTL defaults to 5 minutes — enough for a redirect to complete.
    """
    payload = {
        "dir_name": dir_name,
        "entries": [{"arcname": a, "r2_key": k} for a, k in entries],
        "exp": int(time.time()) + ttl,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{payload_b64}.{sig_b64}"
