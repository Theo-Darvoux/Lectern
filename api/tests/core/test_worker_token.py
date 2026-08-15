"""Tests for app.core.security.worker_token — HMAC-SHA256 signed tokens for the Cloudflare Worker.

Both make_zip_token (directory ZIP) and make_file_token (single-file edge serve)
produce two-part tokens: <base64url(payload_json)>.<base64url(hmac_sha256)>.
The Cloudflare Worker verifies these before serving content.

Tests verify:
- Structural format (two dot-separated parts)
- Payload field contents
- HMAC-SHA256 signature correctness
- Expiry window (exp ~ now + ttl)
- Optional fields are present only when specified
- Signature sensitivity to secret and payload changes
"""

import base64
import hashlib
import hmac
import json
import time

from app.core.security.worker_token import make_file_token, make_zip_token

SECRET = "test-hmac-secret"


def _decode_token(token: str) -> tuple[dict, str, str]:
    """Split and decode a worker token; return (payload, payload_b64, sig_b64)."""
    assert token.count(".") == 1, f"Expected exactly one '.', got: {token!r}"
    payload_b64, sig_b64 = token.split(".")
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    return payload, payload_b64, sig_b64


def _recompute_sig(payload_b64: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


# ── make_zip_token ───────────────────────────────────────────────────────────


def test_zip_token_two_part_format() -> None:
    token = make_zip_token("MyDir", [("file.pdf", "r2/key.pdf")], secret=SECRET)
    assert token.count(".") == 1


def test_zip_token_payload_dir_name_and_entries() -> None:
    entries = [("a.pdf", "r2/a.pdf"), ("b.pdf", "r2/b.pdf")]
    token = make_zip_token("MyDir", entries, secret=SECRET)
    payload, _, _ = _decode_token(token)
    assert payload["dir_name"] == "MyDir"
    assert payload["entries"] == [
        {"arcname": "a.pdf", "r2_key": "r2/a.pdf"},
        {"arcname": "b.pdf", "r2_key": "r2/b.pdf"},
    ]


def test_zip_token_exp_within_ttl_window() -> None:
    ttl = 300
    before = int(time.time())
    token = make_zip_token("D", [], secret=SECRET, ttl=ttl)
    after = int(time.time())
    payload, _, _ = _decode_token(token)
    # exp = int(time.time()) + ttl so it is in [before+ttl, after+ttl]
    assert before + ttl <= payload["exp"] <= after + ttl


def test_zip_token_signature_is_valid() -> None:
    token = make_zip_token("D", [("f.pdf", "k")], secret=SECRET)
    _, payload_b64, sig_b64 = _decode_token(token)
    assert sig_b64 == _recompute_sig(payload_b64, SECRET)


def test_zip_token_wrong_secret_fails_verification() -> None:
    token = make_zip_token("D", [], secret=SECRET)
    _, payload_b64, sig_b64 = _decode_token(token)
    assert sig_b64 != _recompute_sig(payload_b64, "wrong-secret")


def test_zip_token_part_and_total_present_when_specified() -> None:
    token = make_zip_token("D", [], secret=SECRET, part=2, total=3)
    payload, _, _ = _decode_token(token)
    assert payload["part"] == 2
    assert payload["total"] == 3


def test_zip_token_part_and_total_absent_by_default() -> None:
    token = make_zip_token("D", [], secret=SECRET)
    payload, _, _ = _decode_token(token)
    assert "part" not in payload
    assert "total" not in payload


def test_zip_token_payload_tampering_invalidates_signature() -> None:
    token = make_zip_token("D", [], secret=SECRET)
    payload_b64, sig_b64 = token.split(".")
    # Flip the last character of the encoded payload
    tampered = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
    assert sig_b64 != _recompute_sig(tampered, SECRET)


def test_zip_token_empty_entries() -> None:
    token = make_zip_token("Empty", [], secret=SECRET)
    payload, _, _ = _decode_token(token)
    assert payload["entries"] == []


# ── make_file_token ──────────────────────────────────────────────────────────


def test_file_token_two_part_format() -> None:
    token = make_file_token("r2/key.pdf", secret=SECRET)
    assert token.count(".") == 1


def test_file_token_payload_r2_key() -> None:
    token = make_file_token("r2/key.pdf", secret=SECRET)
    payload, _, _ = _decode_token(token)
    assert payload["r2_key"] == "r2/key.pdf"


def test_file_token_exp_within_ttl_window() -> None:
    ttl = 900
    before = int(time.time())
    token = make_file_token("k", secret=SECRET, ttl=ttl)
    after = int(time.time())
    payload, _, _ = _decode_token(token)
    assert before + ttl <= payload["exp"] <= after + ttl


def test_file_token_signature_is_valid() -> None:
    token = make_file_token("k", secret=SECRET)
    _, payload_b64, sig_b64 = _decode_token(token)
    assert sig_b64 == _recompute_sig(payload_b64, SECRET)


def test_file_token_force_download_defaults_true() -> None:
    token = make_file_token("k", secret=SECRET)
    payload, _, _ = _decode_token(token)
    assert payload["force_download"] is True


def test_file_token_optional_fields_present_when_specified() -> None:
    token = make_file_token(
        "k",
        secret=SECRET,
        filename="report.pdf",
        content_type="application/pdf",
        force_download=False,
    )
    payload, _, _ = _decode_token(token)
    assert payload["filename"] == "report.pdf"
    assert payload["content_type"] == "application/pdf"
    assert payload["force_download"] is False


def test_file_token_optional_fields_absent_by_default() -> None:
    token = make_file_token("k", secret=SECRET)
    payload, _, _ = _decode_token(token)
    assert "filename" not in payload
    assert "content_type" not in payload


def test_file_token_different_secrets_produce_different_signatures() -> None:
    t1 = make_file_token("k", secret="secret-a")
    t2 = make_file_token("k", secret="secret-b")
    # Signatures differ even for the same key (different payloads because exp may vary,
    # but more importantly the HMAC key differs)
    _, p1_b64, sig1 = _decode_token(t1)
    _, p2_b64, sig2 = _decode_token(t2)
    assert sig1 != _recompute_sig(p1_b64, "secret-b")
    assert sig2 != _recompute_sig(p2_b64, "secret-a")
