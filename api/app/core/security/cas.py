from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)

_LUA_DIR = Path(__file__).parent / "lua"
_LUA_CAS_INCR = (_LUA_DIR / "cas_incr.lua").read_text(encoding="utf-8")
_LUA_CAS_DECR = (_LUA_DIR / "cas_decr.lua").read_text(encoding="utf-8")


def _derive_cas_signing_key() -> bytes:
    """Derive a dedicated CAS signing key from the server's secret key using HKDF."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=settings.cas_hkdf_salt.encode(),
        info=settings.cas_hkdf_info.encode(),
    )
    return hkdf.derive(settings.secret_key.get_secret_value().encode())


_cas_signing_key: bytes | None = None


def hmac_cas_key(sha256: str) -> str:
    """Return the HMAC-keyed Redis CAS key for a file SHA-256."""
    global _cas_signing_key
    if _cas_signing_key is None:
        _cas_signing_key = _derive_cas_signing_key()
    digest = _hmac.new(_cas_signing_key, sha256.encode(), hashlib.sha256).hexdigest()
    return f"upload:cas:{digest}"


_STORAGE_USAGE_KEY = "storage:total_usage_bytes"
_CAS_OPERATION_PREFIX = "cas:operation:"


class CasReferenceError(RuntimeError):
    """CAS reference state could not be changed safely."""


class CasReferenceMissingError(CasReferenceError):
    """The evictable Redis CAS record is missing and needs reconstruction."""


def _operation_marker_key(operation_id: str) -> str:
    if not operation_id:
        raise ValueError("CAS operation_id must not be empty")
    digest = hashlib.sha256(operation_id.encode()).hexdigest()
    return f"{_CAS_OPERATION_PREFIX}{digest}"


async def increment_cas_ref(
    redis: Redis[Any] | Any,
    sha256: str,
    initial_data: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
    *,
    operation_id: str,
) -> int:
    """Idempotently increment the CAS ref count for one durable operation."""
    cas_key = hmac_cas_key(sha256)
    try:
        args: list[object] = []
        if initial_data is not None:
            args.append(json.dumps(initial_data))
        elif ttl_seconds is not None:
            raise ValueError("initial_data is required when setting a CAS staging TTL")
        if ttl_seconds is not None:
            args.append(ttl_seconds)
        keys = [cas_key, _STORAGE_USAGE_KEY, _operation_marker_key(operation_id)]
        count = await redis.eval(_LUA_CAS_INCR, len(keys), *keys, *args)
        result = int(count) if count is not None else -2
        if result == -1:
            raise CasReferenceMissingError(
                "CAS reference is missing; initial_data is required to reconstruct it"
            )
        if result < 0:
            raise CasReferenceError("CAS reference contains invalid data")
        return result
    except CasReferenceError:
        raise
    except Exception as exc:
        raise CasReferenceError(f"CAS ref increment failed for {sha256}") from exc


async def decrement_cas_ref(redis: Redis[Any] | Any, sha256: str, *, operation_id: str) -> int:
    """Idempotently decrement the CAS ref count for one durable operation."""
    cas_key = hmac_cas_key(sha256)
    try:
        keys = [cas_key, _operation_marker_key(operation_id)]
        count = await redis.eval(_LUA_CAS_DECR, len(keys), *keys)
        result = int(count) if count is not None else -2
        if result == -1:
            raise CasReferenceMissingError("CAS reference is missing; refusing destructive cleanup")
        if result < 0:
            raise CasReferenceError("CAS reference contains invalid data")
        return result
    except CasReferenceError:
        raise
    except Exception as exc:
        raise CasReferenceError(f"CAS ref decrement failed for {sha256}") from exc
