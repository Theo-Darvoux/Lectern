from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.redis import get_redis
from app.core.security import decode_token
from app.models.user import User, UserRole
from app.services.auth import is_token_blacklisted
from app.services.user import get_user_by_id

security = HTTPBearer(auto_error=False)

# HTTP methods that never mutate state — the only ones a read-only guest may use.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def _validate_access_payload(
    payload: dict,  # type: ignore[type-arg]
    db: AsyncSession,
    redis: Redis,  # type: ignore[type-arg]
) -> User:
    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid token type")

    jti = payload.get("jti")
    if jti and await is_token_blacklisted(redis, jti):
        raise UnauthorizedError("Token has been revoked")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token payload")

    user = await get_user_by_id(db, user_id)
    if not user:
        raise UnauthorizedError("User not found")

    return user


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> User:
    if not credentials:
        raise UnauthorizedError()

    try:
        payload = decode_token(credentials.credentials)
    except InvalidTokenError:
        raise UnauthorizedError("Invalid token")

    user = await _validate_access_payload(payload, db, redis)

    if user.role == UserRole.PENDING:
        raise ForbiddenError("Account pending admin approval", code="USER_PENDING")

    # Central read-only enforcement for guests: every authenticated endpoint
    # funnels through this dependency, so blocking unsafe methods here makes the
    # whole API read-only for guests in one place. Logout is exempt so a guest
    # can still end its own session.
    if (
        user.role == UserRole.GUEST
        and request.method not in _SAFE_METHODS
        and not request.url.path.rstrip("/").endswith("/auth/logout")
    ):
        raise ForbiddenError("Guests have read-only access", code="GUEST_READ_ONLY")

    return user


async def get_optional_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> User | None:
    if not credentials:
        return None

    try:
        return await get_current_user(request, credentials, db, redis)
    except UnauthorizedError:
        return None


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole, message: str = "Insufficient permissions"):  # type: ignore[no-untyped-def]
    async def check_role(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ForbiddenError(message)
        return user

    return check_role


def require_onboarded():  # type: ignore[no-untyped-def]
    async def check_onboarded(user: CurrentUser) -> User:
        if not user.onboarded:
            raise ForbiddenError("Onboarding required")
        return user

    return check_onboarded


def require_moderator():  # type: ignore[no-untyped-def]
    return require_role(UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX)


def require_not_guest(message: str = "Guests cannot access this resource"):  # type: ignore[no-untyped-def]
    """Reject guests entirely — used for areas guests may not even read (e.g. PRs)."""

    async def check_not_guest(user: CurrentUser) -> User:
        if user.role == UserRole.GUEST:
            raise ForbiddenError(message, code="GUEST_FORBIDDEN")
        return user

    return check_not_guest


OnboardedUser = Annotated[User, Depends(require_onboarded())]


async def get_user_from_token(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    token: Annotated[str | None, Query()] = None,
) -> User:
    """Authenticate via query-param JWT (useful when headers can't be set)."""
    if not token:
        raise UnauthorizedError("Token required as query parameter")

    try:
        payload = decode_token(token)
    except InvalidTokenError:
        raise UnauthorizedError("Invalid token")

    return await _validate_access_payload(payload, db, redis)


async def get_sse_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    token: Annotated[str | None, Query()] = None,
) -> User:
    """Authenticate via query-param JWT (EventSource cannot send headers)."""
    return await get_user_from_token(db, redis, token)


QueryTokenUser = Annotated[User, Depends(get_user_from_token)]


SSEUser = Annotated[User, Depends(get_sse_user)]
