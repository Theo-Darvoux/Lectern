import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

logger = logging.getLogger(__name__)

import google.auth.transport.requests
from fastapi import APIRouter, Depends, Request, Response
from google.oauth2 import id_token
from redis.asyncio import Redis
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, RateLimitError, UnauthorizedError
from app.core.redis import get_redis
from app.core.security import decode_token
from app.dependencies.auth import CurrentUser
from app.models.user import User, UserRole
from app.schemas.auth import (
    GoogleLoginIn,
    LoginIn,
    RefreshResponse,
    RequestCodeIn,
    TokenResponse,
    UserBrief,
    VerifyCodeIn,
    VerifyMagicLinkIn,
)
from app.services import auth as auth_service
from app.services.email import send_verification_email
from app.services.notification import notify_admins_pending_user
from app.services.user import get_user_by_id

router = APIRouter(prefix="/api/auth", tags=["auth"])

GUEST_SESSION_EXPIRE_DAYS = 1


async def require_client_id(request: Request) -> None:
    if not request.headers.get("x-client-id"):
        raise UnauthorizedError("Missing Client-ID header (CSRF Protection)")


def get_client_id(request: Request) -> str:
    ip = get_remote_address(request)
    client_id = request.headers.get("x-client-id", "unknown")

    return f"{ip}:{client_id}"


limiter = Limiter(key_func=get_client_id, enabled=not settings.is_dev)


def _set_refresh_cookie(response: Response, token: str, expire_days: int | None = None) -> None:
    days = expire_days if expire_days is not None else settings.jwt_refresh_token_expire_days
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=days * 24 * 3600,
        path="/api/auth/",
    )


def _login_response(user: User, response: Response, *, is_new: bool) -> TokenResponse:
    access_token, refresh_token, _ = auth_service.issue_tokens(user)
    _set_refresh_cookie(response, refresh_token)
    return TokenResponse(
        access_token=access_token,
        user=UserBrief(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            role=user.role.value,
            onboarded=user.onboarded,
            auto_approve=user.auto_approve,
        ),
        is_new_user=is_new,
    )


@router.get("/methods")
async def get_auth_methods() -> dict[str, Any]:
    return {
        "totp_enabled": settings.totp_enabled,
        "google_enabled": settings.google_oauth_enabled,
        "google_client_id": settings.google_client_id,
        "classic_enabled": settings.classic_auth_enabled,
        "allow_all_domains": settings.allow_all_domains,
        "guest_access_enabled": settings.guest_access_enabled,
        "site_name": settings.site_name,
        "site_name_style": settings.site_name_style,
        "site_description": settings.site_description,
        "site_logo_url": settings.site_logo_url,
        "site_favicon_url": settings.site_favicon_url,
        "primary_color": settings.primary_color,
        "footer_text": settings.footer_text,
        "footer_logo_url": settings.footer_logo_url,
        "organization_url": settings.organization_url,
        "og_image_url": settings.og_image_url,
        "bg_watermark_url": settings.bg_watermark_url,
        "bg_watermark_opacity_light": settings.bg_watermark_opacity_light,
        "bg_watermark_opacity_dark": settings.bg_watermark_opacity_dark,
        "legal_name": settings.legal_name,
        "legal_address": settings.legal_address,
        "legal_siret": settings.legal_siret,
        "contact_email": settings.contact_email,
        "dpo_email": settings.dpo_email,
        "dpo_address": settings.dpo_address,
        "data_transfers": settings.data_transfers,
        "legal_version": settings.legal_version,
    }


@router.post("/guest", response_model=TokenResponse)
async def guest_session(
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> TokenResponse:
    """Start a read-only guest session when an admin has enabled guest access."""
    if not settings.guest_access_enabled:
        raise UnauthorizedError("Guest access is disabled")

    guest = await auth_service.get_guest_user(db)
    if guest is None:
        raise UnauthorizedError("Guest access is unavailable")

    # Guest sessions are deliberately short-lived; there is nothing to persist.
    access_token, refresh_token, _ = auth_service.issue_tokens(
        guest,
        jwt_access_expire_days=GUEST_SESSION_EXPIRE_DAYS,
        jwt_refresh_expire_days=GUEST_SESSION_EXPIRE_DAYS,
    )
    _set_refresh_cookie(response, refresh_token, GUEST_SESSION_EXPIRE_DAYS)
    return TokenResponse(
        access_token=access_token,
        user=UserBrief(
            id=str(guest.id),
            email=guest.email,
            display_name=guest.display_name,
            avatar_url=guest.avatar_url,
            role=guest.role.value,
            onboarded=guest.onboarded,
            auto_approve=guest.auto_approve,
        ),
        is_new_user=False,
    )


@router.post("/request-code")
@limiter.limit("3/15minutes" if not settings.is_dev else "10000/minute")
async def request_code(
    request: Request,
    data: RequestCodeIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> dict[str, str]:
    email = data.email

    if not settings.totp_enabled:
        raise UnauthorizedError("Email verification codes are disabled")

    try:
        await auth_service.validate_email_for_auth(email, db)
    except ValueError as exc:
        raise BadRequestError(str(exc))

    if not await auth_service.check_rate_limit(redis, email):
        raise RateLimitError(
            "Too many code requests. You can request up to 3 codes per 15 minutes. Please wait before trying again."
        )

    code = auth_service.generate_code()
    await auth_service.store_code(redis, email, code)

    magic_token = auth_service.generate_magic_token()
    await auth_service.store_magic_token(redis, email, magic_token)

    base_url = settings.frontend_url.rstrip("/")
    magic_link = f"{base_url}/login/verify?token={magic_token}"

    try:
        await send_verification_email(email, code, magic_link)
    except Exception as e:
        logger.error("Failed to send verification email: %s", e, exc_info=True)

    return {"message": "Verification code sent"}


@router.post("/verify-code", response_model=TokenResponse)
async def verify_code(
    data: VerifyCodeIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    response: Response,
) -> TokenResponse:
    email = data.email.strip().lower()

    if not await auth_service.check_verify_rate_limit(redis, email):
        raise RateLimitError(
            "Too many verification attempts. Please wait 10 minutes before trying again."
        )

    if not await auth_service.verify_code(redis, email, data.code):
        await auth_service.increment_verify_rate_limit(redis, email)
        raise BadRequestError("Invalid or expired verification code")

    await auth_service.reset_verify_rate_limit(redis, email)
    # Re-validate to get auto_approve; ValidationError is possible if domain
    # was removed between request-code and verify-code steps.
    try:
        auto_approve = await auth_service.validate_email_for_auth(email, db)
    except ValueError:
        auto_approve = False
    user, is_new = await auth_service.get_or_create_user(db, email, auto_approve=auto_approve)
    if is_new and user.role == UserRole.PENDING:
        await notify_admins_pending_user(db, user)
    return _login_response(user, response, is_new=is_new)


@router.post("/verify-magic-link", response_model=TokenResponse)
@limiter.limit("10/15minutes" if not settings.is_dev else "10000/minute")
async def verify_magic_link(
    request: Request,
    data: VerifyMagicLinkIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    response: Response,
) -> TokenResponse:
    email = await auth_service.verify_magic_token(redis, data.token)
    if not email:
        raise BadRequestError("Invalid or expired magic link")

    await auth_service.reset_verify_rate_limit(redis, email)
    try:
        auto_approve = await auth_service.validate_email_for_auth(email, db)
    except ValueError:
        auto_approve = False
    user, is_new = await auth_service.get_or_create_user(db, email, auto_approve=auto_approve)
    if is_new and user.role == UserRole.PENDING:
        await notify_admins_pending_user(db, user)
    return _login_response(user, response, is_new=is_new)


@router.post("/google", response_model=TokenResponse)
async def verify_google_oauth(
    data: GoogleLoginIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> TokenResponse:
    if not settings.google_oauth_enabled:
        raise UnauthorizedError("Google OAuth is disabled")

    try:
        # id_token.verify_oauth2_token makes a blocking HTTP call to fetch Google's
        # JWKS endpoint on cache miss.  Run in a thread to avoid stalling the loop.
        id_info = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            data.credential,
            google.auth.transport.requests.Request(),
            settings.google_client_id,
        )
    except Exception as e:
        logger.error("Google OAuth verification failed: %s", e, exc_info=True)
        raise UnauthorizedError("Invalid Google credential")

    if id_info.get("iss") not in ["accounts.google.com", "https://accounts.google.com"]:
        raise UnauthorizedError("Invalid Google issuer")

    if not id_info.get("email_verified"):
        raise UnauthorizedError("Google account email address is not verified")

    email = id_info.get("email")
    if not email:
        raise BadRequestError("Email not provided by Google")

    email = email.lower().strip()

    # Enforce domain whitelisting / auto-approve rules
    try:
        auto_approve = await auth_service.validate_email_for_auth(email, db)
    except ValueError as exc:
        raise BadRequestError(str(exc))

    user, is_new = await auth_service.get_or_create_user(db, email, auto_approve=auto_approve)

    # Enrich user profile if it's a new or existing user missing data
    updated = False
    given_name = id_info.get("given_name")
    family_name = id_info.get("family_name")
    picture = id_info.get("picture")

    if is_new or not user.display_name:
        names = [n for n in (given_name, family_name) if n]
        if names:
            user.display_name = " ".join(names)
            updated = True

    if (is_new or not user.avatar_url) and picture:
        user.avatar_url = picture
        updated = True

    if updated:
        await db.flush()

    if is_new and user.role == UserRole.PENDING:
        await notify_admins_pending_user(db, user)

    return _login_response(user, response, is_new=is_new)


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> TokenResponse:
    if not settings.classic_auth_enabled:
        raise UnauthorizedError("Classic authentication (email + password) is disabled")

    user = await auth_service.authenticate_user(db, data.email, data.password)
    if not user:
        raise UnauthorizedError("Invalid email or password")

    user.last_login_at = datetime.now(UTC)
    await db.flush()

    return _login_response(user, response, is_new=False)


@router.post("/refresh", response_model=RefreshResponse, dependencies=[Depends(require_client_id)])
async def refresh_token(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> RefreshResponse:
    token = request.cookies.get("refresh_token")
    if not token:
        raise UnauthorizedError("No refresh token")

    try:
        payload = decode_token(token)
    except Exception:
        raise UnauthorizedError("Invalid refresh token")

    if payload.get("type") != "refresh":
        raise UnauthorizedError("Invalid token type")

    jti = payload.get("jti")
    if jti and await auth_service.is_token_blacklisted(redis, jti):
        raise UnauthorizedError("Refresh token has been revoked")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token")

    user = await get_user_by_id(db, user_id)
    if not user:
        raise UnauthorizedError("User not found")

    old_jti = payload.get("jti")
    old_exp = payload.get("exp")
    if old_jti and old_exp:
        remaining = int(old_exp - datetime.now(UTC).timestamp())
        if remaining > 0:
            await auth_service.blacklist_token(redis, old_jti, remaining)

    new_access_token, new_refresh_token, _ = auth_service.issue_tokens(user)
    _set_refresh_cookie(response, new_refresh_token)

    return RefreshResponse(access_token=new_access_token)


@router.post("/logout", dependencies=[Depends(require_client_id)])
async def logout(
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    request: Request,
    response: Response,
) -> dict[str, str]:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = decode_token(token)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                remaining = int(exp - datetime.now(UTC).timestamp())
                if remaining > 0:
                    await auth_service.blacklist_token(redis, jti, remaining)
        except Exception:
            pass

    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        try:
            refresh_payload = decode_token(refresh_token)
            refresh_jti = refresh_payload.get("jti")
            refresh_exp = refresh_payload.get("exp")
            if refresh_jti and refresh_exp:
                remaining = int(refresh_exp - datetime.now(UTC).timestamp())
                if remaining > 0:
                    await auth_service.blacklist_token(redis, refresh_jti, remaining)
        except Exception:
            pass

    response.delete_cookie(
        key="refresh_token",
        path="/api/auth/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    return {"message": "Logged out"}
