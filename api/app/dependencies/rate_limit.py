import logging
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.constants import PRIVILEGED_ROLES
from app.core.common.exceptions import BadRequestError, ForbiddenError, RateLimitError
from app.core.database.database import get_db
from app.core.database.redis import get_redis
from app.core.security.security import BROWSER_READ_COOKIE, decode_token
from app.dependencies.auth import CurrentUser
from app.models.user import User, UserRole
from app.services.audit import flag_user_account

logger = logging.getLogger(__name__)


def _request_session_id(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    candidates: list[tuple[str, str]] = []
    if auth_header.startswith("Bearer "):
        candidates.append((auth_header[7:], "access"))
    browser_token = request.cookies.get(BROWSER_READ_COOKIE)
    if browser_token:
        candidates.append((browser_token, "browser_read"))

    for token, expected_type in candidates:
        try:
            payload = decode_token(token, expected_type=expected_type)
        except Exception:
            logger.debug("Failed to decode token for session ID extraction", exc_info=True)
            continue
        session_id = payload.get("sid")
        if session_id:
            return str(session_id)
    return None


def _guest_source_subject(request: Request) -> str:
    host = (request.client.host if request.client else None) or "unknown"
    return f"guest-ip:{host}"


def _rate_limit_subject(request: Request, user: User) -> str:
    if user.role != UserRole.GUEST:
        return str(user.id)

    session_id = _request_session_id(request)
    if session_id:
        return f"guest-session:{session_id}"

    # Backward-compatible fallback for a pre-session-family guest JWT.
    return _guest_source_subject(request)


def _rate_limit_subjects(request: Request, user: User) -> list[str]:
    """Return the per-session subject plus a stable aggregate guest budget.

    A guest can mint a new session ID, so a session-only counter is not an abuse
    boundary. Authenticated users retain their per-account budget; guests consume
    both the session budget and the trusted client-source budget.
    """
    primary = _rate_limit_subject(request, user)
    if user.role != UserRole.GUEST:
        return [primary]
    return list(dict.fromkeys((primary, _guest_source_subject(request))))


async def rate_limit_downloads(
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    count: int = 1,
) -> None:
    minute_limit = 100 if settings.is_dev else 10
    daily_limit = 2000 if settings.is_dev else 200

    subjects = _rate_limit_subjects(request, user)

    async with redis.pipeline(transaction=True) as pipe:
        for subject in subjects:
            minute_key = f"ratelimit:downloads:min:{subject}"
            daily_key = f"ratelimit:downloads:day:{subject}"
            await pipe.incrby(minute_key, count)
            await pipe.expire(minute_key, 60, nx=True)
            await pipe.incrby(daily_key, count)
            await pipe.expire(daily_key, 86400, nx=True)
        results = await pipe.execute()

    minute_counts = [int(results[offset]) for offset in range(0, len(results), 4)]
    daily_counts = [int(results[offset + 2]) for offset in range(0, len(results), 4)]
    minute_count = max(minute_counts, default=0)
    daily_count = max(daily_counts, default=0)

    if minute_count > minute_limit:
        raise RateLimitError(
            f"You are downloading too fast. Limit: {minute_limit} files per minute."
        )

    if daily_count > daily_limit:
        if user.role != UserRole.GUEST:
            await flag_user_account(
                db, user.id, f"Exceeded daily download limit ({daily_count}/{daily_limit})"
            )
            await db.commit()
        raise RateLimitError(
            f"Daily download limit reached ({daily_limit} files). Please try again tomorrow."
        )


# Per-role rate limit tiers: (per_minute, per_day) for prod / dev
_UPLOAD_LIMITS: dict[str, tuple[int, int]] = {
    "default": (10, 100) if not settings.is_dev else (100, 1000),
    "privileged": (50, 500) if not settings.is_dev else (200, 5000),
}

UPLOAD_GROUP_KEY_PREFIX = "upload:group:"
_UPLOAD_GROUP_SEEN_PREFIX = "upload:group:seen:"
_UPLOAD_GROUP_ELIGIBLE_PATHS = frozenset(
    {
        "/api/upload",
        "/api/upload/init",
        "/api/upload/presigned-multipart/init",
        "/api/upload/tus",
        "/api/upload/tus/",
    }
)
_CONSUME_UPLOAD_GROUP_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return -1 end
local ok, group = pcall(cjson.decode, raw)
if not ok or tostring(group.user_id) ~= ARGV[1] then return -2 end
if redis.call('SISMEMBER', KEYS[2], ARGV[2]) == 1 then return 1 end
if redis.call('SCARD', KEYS[2]) >= tonumber(group.max_files) then return 0 end
redis.call('SADD', KEYS[2], ARGV[2])
local ttl = redis.call('TTL', KEYS[1])
if ttl > 0 then redis.call('EXPIRE', KEYS[2], ttl) end
return 1
"""


async def _consume_upload_group(request: Request, user: User, redis: Redis) -> bool:  # type: ignore[type-arg]
    group_id = request.headers.get("X-Upload-Group-ID")
    if (
        not group_id
        or request.method != "POST"
        or request.url.path not in _UPLOAD_GROUP_ELIGIBLE_PATHS
    ):
        return False

    if user.role == UserRole.GUEST:
        raise ForbiddenError("Guest uploads cannot use folder admission groups")
    upload_id = request.headers.get("X-Upload-ID")
    if not upload_id:
        raise BadRequestError("X-Upload-ID is required with X-Upload-Group-ID")
    try:
        group_id = str(UUID(group_id))
        upload_id = str(UUID(upload_id))
    except ValueError:
        raise BadRequestError("Upload group and upload IDs must be valid UUIDs")

    script = redis.register_script(_CONSUME_UPLOAD_GROUP_LUA)
    result = int(
        await script(
            keys=[f"{UPLOAD_GROUP_KEY_PREFIX}{group_id}", f"{_UPLOAD_GROUP_SEEN_PREFIX}{group_id}"],
            args=[str(user.id), upload_id],
            client=redis,
        )
    )
    if result == 1:
        return True
    if result == -1:
        raise BadRequestError("Upload folder admission expired; please select the folder again")
    if result == -2:
        raise ForbiddenError("Upload folder admission does not belong to you")
    raise RateLimitError("Upload folder admission contains no remaining file slots")


async def rate_limit_uploads(
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> None:
    if await _consume_upload_group(request, user, redis):
        return

    tier = "privileged" if user.role in PRIVILEGED_ROLES else "default"
    minute_limit, daily_limit = _UPLOAD_LIMITS[tier]

    subjects = _rate_limit_subjects(request, user)

    async with redis.pipeline(transaction=True) as pipe:
        for subject in subjects:
            minute_key = f"ratelimit:uploads:min:{subject}"
            daily_key = f"ratelimit:uploads:day:{subject}"
            await pipe.incr(minute_key)
            await pipe.expire(minute_key, 60, nx=True)
            await pipe.incr(daily_key)
            await pipe.expire(daily_key, 86400, nx=True)
        results = await pipe.execute()

    minute_count = max(int(results[offset]) for offset in range(0, len(results), 4))
    daily_count = max(int(results[offset + 2]) for offset in range(0, len(results), 4))

    if minute_count > minute_limit:
        raise RateLimitError(f"You are uploading too fast. Limit: {minute_limit} files per minute.")

    if daily_count > daily_limit:
        if user.role != UserRole.GUEST:
            await flag_user_account(
                db, user.id, f"Exceeded daily upload limit ({daily_count}/{daily_limit})"
            )
            await db.commit()
        raise RateLimitError(
            f"Daily upload limit reached ({daily_limit} files). Please try again tomorrow."
        )


async def rate_limit_views(
    request: Request,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> None:
    """Rate limit for view tracking.

    View recording is fire-and-forget on the client (errors are swallowed), so
    exceeding this limit has no user-visible effect — it only caps abusive
    flooding of the endpoint. Limits are deliberately set far above any
    realistic browsing rate, use dedicated counters (they don't consume the
    download/upload budget), and do NOT flag the account, so regular users are
    never impacted.
    """
    minute_limit = 600 if settings.is_dev else 60
    daily_limit = 5000 if settings.is_dev else 1000

    subjects = _rate_limit_subjects(request, user)

    async with redis.pipeline(transaction=True) as pipe:
        for subject in subjects:
            minute_key = f"ratelimit:views:min:{subject}"
            daily_key = f"ratelimit:views:day:{subject}"
            await pipe.incr(minute_key)
            await pipe.expire(minute_key, 60, nx=True)
            await pipe.incr(daily_key)
            await pipe.expire(daily_key, 86400, nx=True)
        results = await pipe.execute()

    minute_count = max(int(results[offset]) for offset in range(0, len(results), 4))
    daily_count = max(int(results[offset + 2]) for offset in range(0, len(results), 4))
    if minute_count > minute_limit or daily_count > daily_limit:
        raise RateLimitError("Too many view events. Please slow down.")


async def rate_limit_search(
    request: Request,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> None:
    """Rate limit for the authenticated search endpoint: 120/min (300 in dev)."""
    subjects = _rate_limit_subjects(request, user)
    keys = [f"ratelimit:search:user:{subject}:min" for subject in subjects]
    minute_limit = 300 if settings.is_dev else 120

    async with redis.pipeline(transaction=True) as pipe:
        for key in keys:
            await pipe.incr(key)
            await pipe.expire(key, 60, nx=True)
        results = await pipe.execute()

    counts = [int(results[offset]) for offset in range(0, len(results), 2)]
    if max(counts, default=0) > minute_limit:
        raise RateLimitError("Too many search requests. Please slow down.")
