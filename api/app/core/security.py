from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt

from app.config import settings

ALGORITHM = "HS256"


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
    token = jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=ALGORITHM)
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
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[ALGORITHM])


def get_jti(token: str) -> str:
    payload = decode_token(token)
    return str(payload["jti"])
