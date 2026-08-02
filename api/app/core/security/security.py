from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt

from app.config import settings

ALGORITHM = "HS256"


def _get_secret_key() -> str:
    return settings.secret_key.get_secret_value()


def create_access_token(
    user_id: str, role: str, email: str, expire_days: int | None = None
) -> tuple[str, str]:
    jti = str(uuid4())
    days = expire_days if expire_days is not None else settings.jwt_access_token_expire_days
    expire = datetime.now(UTC) + timedelta(days=days)
    payload = {
        "sub": user_id,
        "role": role,
        "email": email,
        "jti": jti,
        "exp": expire,
        "type": "access",
    }
    token = jwt.encode(payload, _get_secret_key(), algorithm=ALGORITHM)
    return token, jti


def create_refresh_token(user_id: str, expire_days: int | None = None) -> str:
    jti = str(uuid4())
    days = expire_days if expire_days is not None else settings.jwt_refresh_token_expire_days
    expire = datetime.now(UTC) + timedelta(days=days)
    payload = {
        "sub": user_id,
        "jti": jti,
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, _get_secret_key(), algorithm=ALGORITHM)


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    """Decode and verify a JWT token, optionally asserting the expected 'type' claim."""
    payload = jwt.decode(token, _get_secret_key(), algorithms=[ALGORITHM])
    if expected_type is not None and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(
            f"Invalid token type: expected {expected_type!r}, got {payload.get('type')!r}"
        )
    return payload


def get_jti(token: str) -> str:
    payload = decode_token(token)
    return str(payload["jti"])
