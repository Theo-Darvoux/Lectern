"""Tests for app.core.security — JWT creation and verification.

create_access_token / create_refresh_token produce ALGORITHM=HS256 tokens
signed with settings.secret_key.  decode_token verifies and decodes them.
These functions are used everywhere in the app as utilities, but their
actual payload contents and error paths are not covered by integration tests.
"""

import time
from uuid import uuid4

import jwt
import pytest

from app.config import settings
from app.core.security.security import (
    ALGORITHM,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_jti,
)

_SECRET = settings.secret_key.get_secret_value()


def _expired_token() -> str:
    """Construct a token with an exp 10 seconds in the past."""
    return jwt.encode(
        {
            "sub": "test-user",
            "exp": int(time.time()) - 10,
            "type": "access",
            "jti": str(uuid4()),
        },
        _SECRET,
        algorithm=ALGORITHM,
    )


# ── create_access_token ──────────────────────────────────────────────────────


def test_access_token_returns_token_and_jti() -> None:
    token, jti = create_access_token("uid-1", "student", "test@example.com")
    assert isinstance(token, str)
    assert isinstance(jti, str)
    assert len(jti) > 0


def test_access_token_payload_sub_role_email() -> None:
    token, jti = create_access_token("uid-1", "student", "test@example.com")
    payload = decode_token(token)
    assert payload["sub"] == "uid-1"
    assert payload["role"] == "student"
    assert payload["email"] == "test@example.com"


def test_access_token_payload_type_and_jti() -> None:
    token, jti = create_access_token("uid-1", "student", "test@example.com")
    payload = decode_token(token)
    assert payload["type"] == "access"
    assert payload["jti"] == jti


def test_access_token_exp_is_in_future() -> None:
    token, _ = create_access_token("uid", "student", "a@example.com")
    payload = decode_token(token)
    assert payload["exp"] > time.time()


def test_access_token_custom_expire_days() -> None:
    token, _ = create_access_token("uid", "student", "a@example.com", expire_days=1)
    payload = decode_token(token)
    expected_exp = time.time() + 86400
    assert abs(payload["exp"] - expected_exp) < 5


def test_access_token_unique_jti_per_call() -> None:
    _, jti1 = create_access_token("uid", "student", "a@example.com")
    _, jti2 = create_access_token("uid", "student", "a@example.com")
    assert jti1 != jti2


# ── create_refresh_token ─────────────────────────────────────────────────────


def test_refresh_token_payload_type_is_refresh() -> None:
    token = create_refresh_token("uid-2")
    payload = decode_token(token)
    assert payload["type"] == "refresh"


def test_refresh_token_payload_has_sub_and_jti() -> None:
    token = create_refresh_token("uid-2")
    payload = decode_token(token)
    assert payload["sub"] == "uid-2"
    assert "jti" in payload


def test_refresh_token_exp_is_in_future() -> None:
    token = create_refresh_token("uid")
    payload = decode_token(token)
    assert payload["exp"] > time.time()


def test_refresh_token_unique_jti_per_call() -> None:
    t1 = create_refresh_token("uid")
    t2 = create_refresh_token("uid")
    assert decode_token(t1)["jti"] != decode_token(t2)["jti"]


# ── decode_token ─────────────────────────────────────────────────────────────


def test_decode_token_expired_raises_expired_signature_error() -> None:
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(_expired_token())


def test_decode_token_tampered_signature_raises() -> None:
    token, _ = create_access_token("uid", "student", "a@example.com")
    header_payload, sig = token.rsplit(".", 1)
    tampered = f"{header_payload}.{sig[:-4]}XXXX"
    with pytest.raises((jwt.InvalidSignatureError, jwt.DecodeError)):
        decode_token(tampered)


def test_decode_token_garbage_raises_decode_error() -> None:
    with pytest.raises(jwt.DecodeError):
        decode_token("not-a-valid-jwt")


def test_decode_token_wrong_key_raises() -> None:
    token = jwt.encode({"sub": "uid", "exp": int(time.time()) + 3600}, "other-secret", ALGORITHM)
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token)


# ── get_jti ──────────────────────────────────────────────────────────────────


def test_get_jti_extracts_correct_jti() -> None:
    token, jti = create_access_token("uid", "student", "a@example.com")
    assert get_jti(token) == jti
