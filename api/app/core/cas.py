from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger("wikint.cas")

_CAS_INFO = b"wikint-cas-v1"


def _derive_cas_signing_key() -> bytes:
    """Derive a dedicated CAS signing key from the server's secret key using HKDF."""
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"wikint-cas-salt-v1", info=_CAS_INFO)
    return hkdf.derive(settings.secret_key.get_secret_value().encode())


_cas_signing_key: bytes | None = None


def hmac_cas_key(sha256: str) -> str:
    """Return the HMAC-keyed Redis CAS key for a file SHA-256.

    The HMAC prevents cross-user probing: a user who knows their file's SHA-256
    cannot compute the CAS key for a file they have never seen.

    Uses HKDF to derive a dedicated signing key from the server's secret,
    ensuring domain separation between CAS and other HMAC uses.
    """
    global _cas_signing_key
    if _cas_signing_key is None:
        _cas_signing_key = _derive_cas_signing_key()
    digest = _hmac.new(_cas_signing_key, sha256.encode(), hashlib.sha256).hexdigest()
    return f"upload:cas:{digest}"


_STORAGE_USAGE_KEY = "storage:total_usage_bytes"


_LUA_CAS_INCR = """
local raw = redis.call('GET', KEYS[1])
local usage_key = KEYS[2]
local data
if not raw then
  if ARGV[1] then
    data = cjson.decode(ARGV[1])
    data['ref_count'] = 1
    -- Physical storage increment: only on first creation
    if data['size'] then
        redis.call('INCRBY', usage_key, data['size'])
    end
  else
    return 0
  end
else
  local ok, decoded = pcall(cjson.decode, raw)
  if not ok then return 0 end
  data = decoded
  data['ref_count'] = (data['ref_count'] or 1) + 1
  if ARGV[1] then
    local arg_data = cjson.decode(ARGV[1])
    if arg_data['scanned_at'] then
      data['scanned_at'] = arg_data['scanned_at']
    end
    -- Keep original file_name if present, or update if missing
    if arg_data['file_name'] and not data['file_name'] then
      data['file_name'] = arg_data['file_name']
    end
    -- Also sync mime_type and size if they were missing
    if arg_data['mime_type'] and not data['mime_type'] then
      data['mime_type'] = arg_data['mime_type']
    end
    if arg_data['size'] and not data['size'] then
      data['size'] = arg_data['size']
    end
  end
end
redis.call('SET', KEYS[1], cjson.encode(data))
return data['ref_count']
"""

_LUA_CAS_DECR = """
local raw = redis.call('GET', KEYS[1])
local usage_key = KEYS[2]
if not raw then return 0 end
local ok, data = pcall(cjson.decode, raw)
if not ok then return 0 end
local count = (data['ref_count'] or 1) - 1
if count <= 0 then
  -- Physical storage decrement: only when the blob is actually about to be deleted
  if data['size'] then
    redis.call('DECRBY', usage_key, data['size'])
    -- Clamp to 0 to guard against negative values after a Redis flush or restart.
    local current = tonumber(redis.call('GET', usage_key)) or 0
    if current < 0 then
      redis.call('SET', usage_key, 0)
    end
  end
  redis.call('DEL', KEYS[1])
  return 0
end
data['ref_count'] = count
redis.call('SET', KEYS[1], cjson.encode(data))
return count
"""


async def increment_cas_ref(
    redis: Redis,
    sha256: str,
    initial_data: dict[str, Any] | None = None,  # type: ignore[type-arg]
) -> int:
    """Atomically increment the CAS ref count. Returns the new count, or 0 on error."""
    cas_key = hmac_cas_key(sha256)
    try:
        if initial_data:
            count = await redis.eval(
                _LUA_CAS_INCR, 2, cas_key, _STORAGE_USAGE_KEY, json.dumps(initial_data)
            )  # type: ignore[no-untyped-call]
        else:
            count = await redis.eval(_LUA_CAS_INCR, 2, cas_key, _STORAGE_USAGE_KEY)  # type: ignore[no-untyped-call]
        return int(count) if count is not None else 1
    except Exception as exc:
        logger.warning("CAS ref increment failed for %s: %s", sha256, exc)
        return 0


async def decrement_cas_ref(redis: Redis, sha256: str) -> None:  # type: ignore[type-arg]
    cas_key = hmac_cas_key(sha256)
    try:
        await redis.eval(_LUA_CAS_DECR, 2, cas_key, _STORAGE_USAGE_KEY)  # type: ignore[no-untyped-call]
    except Exception as exc:
        logger.warning("CAS ref decrement failed for %s: %s", sha256, exc)
