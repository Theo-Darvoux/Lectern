"""Tests for the EuroOffice internal token utilities in app.routers.eurooffice.

_create_file_token  — creates a short-lived JWT allowing EuroOffice to fetch a file.
_verify_file_token  — validates signature, type claim, and sub claim.

These are security-critical functions: a bad token lets EuroOffice serve an
arbitrary file to any caller that guesses the URL.  Zero test coverage existed
before this file was added.
"""

import time

import jwt
import pytest

from app.config import settings
from app.routers.eurooffice import _EXT_TO_DOCTYPE, _create_file_token, _verify_file_token

_SECRET = settings.eurooffice_file_token_secret
_ALGORITHM = "HS256"


def _decode_raw(token: str) -> dict:
    return jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])


# ── _create_file_token ───────────────────────────────────────────────────────


def test_create_file_token_is_decodable() -> None:
    token = _create_file_token("mat-123")
    payload = _decode_raw(token)
    assert payload is not None


def test_create_file_token_sub_matches_material_id() -> None:
    token = _create_file_token("mat-123")
    assert _decode_raw(token)["sub"] == "mat-123"


def test_create_file_token_type_claim() -> None:
    token = _create_file_token("mat-123")
    assert _decode_raw(token)["type"] == "eurooffice_file"


def test_create_file_token_exp_in_future() -> None:
    token = _create_file_token("mat-123")
    assert _decode_raw(token)["exp"] > time.time()


def test_create_file_token_different_ids_differ() -> None:
    t1 = _create_file_token("mat-aaa")
    t2 = _create_file_token("mat-bbb")
    assert t1 != t2


# ── _verify_file_token ───────────────────────────────────────────────────────


def test_verify_file_token_valid() -> None:
    token = _create_file_token("mat-abc")
    assert _verify_file_token(token, "mat-abc") is True


def test_verify_file_token_wrong_material_id() -> None:
    token = _create_file_token("mat-abc")
    assert _verify_file_token(token, "mat-xyz") is False


def test_verify_file_token_expired() -> None:
    expired = jwt.encode(
        {"sub": "mat-123", "type": "eurooffice_file", "exp": int(time.time()) - 10},
        _SECRET,
        algorithm=_ALGORITHM,
    )
    assert _verify_file_token(expired, "mat-123") is False


def test_verify_file_token_wrong_type_claim() -> None:
    wrong_type = jwt.encode(
        {"sub": "mat-123", "type": "access", "exp": int(time.time()) + 600},
        _SECRET,
        algorithm=_ALGORITHM,
    )
    assert _verify_file_token(wrong_type, "mat-123") is False


def test_verify_file_token_tampered_signature() -> None:
    token = _create_file_token("mat-123")
    header_payload, sig = token.rsplit(".", 1)
    tampered = f"{header_payload}.{sig[:-4]}XXXX"
    assert _verify_file_token(tampered, "mat-123") is False


def test_verify_file_token_garbage_returns_false() -> None:
    assert _verify_file_token("not-a-jwt", "mat-123") is False


def test_verify_file_token_signed_with_wrong_secret() -> None:
    token = jwt.encode(
        {"sub": "mat-123", "type": "eurooffice_file", "exp": int(time.time()) + 600},
        "wrong-secret-key-that-is-at-least-32-bytes-long",
        algorithm=_ALGORITHM,
    )
    assert _verify_file_token(token, "mat-123") is False


# ── _EXT_TO_DOCTYPE mapping ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ext, expected_doc_type",
    [
        ("docx", "word"),
        ("doc", "word"),
        ("odt", "word"),
        ("xlsx", "cell"),
        ("xls", "cell"),
        ("ods", "cell"),
        ("pptx", "slide"),
        ("ppt", "slide"),
        ("pdf", "pdf"),
    ],
)
def test_ext_to_doctype_known_extensions(ext: str, expected_doc_type: str) -> None:
    assert _EXT_TO_DOCTYPE[ext] == expected_doc_type


def test_ext_to_doctype_unknown_extension_not_present() -> None:
    # Unknown extensions fall back to "word" via .get(ext, "word") in the router.
    assert "txt" not in _EXT_TO_DOCTYPE
    assert "mp4" not in _EXT_TO_DOCTYPE
