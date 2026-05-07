from typing import Annotated

from fastapi import Depends, Query
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

    return user


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> User | None:
    if not credentials:
        return None

    try:
        return await get_current_user(credentials, db, redis)
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
