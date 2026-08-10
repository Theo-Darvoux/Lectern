import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

logger = logging.getLogger(__name__)

import google.auth.transport.requests
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from google.oauth2 import id_token
from redis.asyncio import Redis
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.exceptions import (
    BadRequestError,
    ConflictError,
    RateLimitError,
    UnauthorizedError,
)
from app.core.database.database import get_db
from app.core.database.redis import get_redis
from app.core.security.security import (
    BROWSER_READ_COOKIE,
    create_browser_read_token,
    decode_token,
)
from app.dependencies.auth import CurrentUser
from app.models.user import User, UserRole
from app.schemas.auth import (
    GoogleLoginIn,
    LoginIn,
    RefreshResponse,
    RequestCodeIn,
    SetupIn,
    TokenResponse,
    UserBrief,
    VerifyCodeIn,
    VerifyMagicLinkIn,
)
from app.schemas.pull_request import MAX_PR_DESCRIPTION_LENGTH
from app.services import auth as auth_service
from app.services.avatar import is_safe_avatar_reference, is_trusted_external_avatar_url
from app.services.email import send_verification_email
from app.services.notification import notify_admins_pending_user
from app.services.user import get_user_by_id

router = APIRouter(prefix="/api/auth", tags=["auth"])

GUEST_SESSION_EXPIRE_DAYS = 1


async def require_client_id(request: Request) -> None:
    if not request.headers.get("x-client-id"):
        raise UnauthorizedError("Missing Client-ID header (CSRF Protection)")


def get_client_id(request: Request) -> str:
    # X-Client-ID is client-controlled and can be rotated trivially. The proxy
    # middleware has already limited forwarding headers to trusted peers.
    return get_remote_address(request)


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


def _set_browser_read_cookie(
    response: Response,
    user_id: str,
    expire_days: int | None = None,
    *,
    session_id: str | None = None,
    auth_generation: int = 0,
) -> None:
    """Set the browser-native credential used only by read-only API routes."""
    days = expire_days if expire_days is not None else settings.jwt_access_token_expire_days
    response.set_cookie(
        key=BROWSER_READ_COOKIE,
        value=create_browser_read_token(
            user_id,
            expire_days=days,
            session_id=session_id,
            auth_generation=auth_generation,
        ),
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=days * 24 * 3600,
        path="/api/",
    )


async def _blacklist_token_string(
    redis: Redis,  # type: ignore[type-arg]
    token: str | None,
    *,
    expected_type: str,
) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type=expected_type)
    except Exception:
        return None

    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        remaining = int(exp - datetime.now(UTC).timestamp())
        if remaining > 0:
            await auth_service.blacklist_token(redis, str(jti), remaining)
    return payload


async def _blacklist_browser_read_cookie(
    redis: Redis,  # type: ignore[type-arg]
    request: Request,
) -> dict[str, Any] | None:
    return await _blacklist_token_string(
        redis,
        request.cookies.get(BROWSER_READ_COOKIE),
        expected_type="browser_read",
    )


def _session_ttl_seconds(user: User) -> int:
    days = (
        GUEST_SESSION_EXPIRE_DAYS
        if user.role == UserRole.GUEST
        else settings.jwt_refresh_token_expire_days
    )
    return max(1, days * 24 * 3600)


def _login_response(user: User, response: Response, *, is_new: bool) -> TokenResponse:
    session_id = str(uuid4())
    access_token, refresh_token, _ = auth_service.issue_tokens(
        user,
        session_id=session_id,
    )
    _set_refresh_cookie(response, refresh_token)
    _set_browser_read_cookie(
        response,
        str(user.id),
        session_id=session_id,
        auth_generation=user.auth_generation,
    )
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
async def get_auth_methods(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    bootstrapped = await auth_service.installation_bootstrapped(db)
    return {
        "needs_setup": not bootstrapped,
        "bootstrap_token_required": (not bootstrapped and auth_service.bootstrap_token_required()),
        "totp_enabled": settings.totp_enabled,
        "google_enabled": settings.google_oauth_enabled,
        "google_client_id": settings.google_client_id,
        "classic_enabled": settings.classic_auth_enabled,
        "allow_all_domains": settings.allow_all_domains,
        "guest_access_enabled": settings.guest_access_enabled,
        "tutorials_enabled": settings.tutorials_enabled,
        "site_name": settings.site_name,
        "site_name_style": settings.site_name_style,
        "site_description": settings.site_description,
        "site_logo_url": settings.site_logo_url,
        "site_favicon_url": settings.site_favicon_url,
        "primary_color": settings.primary_color,
        "footer_text": settings.footer_text,
        "footer_logo_url": settings.footer_logo_url,
        "organization_url": settings.organization_url,
        "repo_url": settings.repo_url,
        "eurooffice_public_url": settings.eurooffice_public_url,
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
        "max_contribution_note_length": MAX_PR_DESCRIPTION_LENGTH,
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
    session_id = str(uuid4())
    access_token, refresh_token, _ = auth_service.issue_tokens(
        guest,
        jwt_access_expire_days=GUEST_SESSION_EXPIRE_DAYS,
        jwt_refresh_expire_days=GUEST_SESSION_EXPIRE_DAYS,
        session_id=session_id,
    )
    _set_refresh_cookie(response, refresh_token, GUEST_SESSION_EXPIRE_DAYS)
    _set_browser_read_cookie(
        response,
        str(guest.id),
        GUEST_SESSION_EXPIRE_DAYS,
        session_id=session_id,
        auth_generation=guest.auth_generation,
    )
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
    background_tasks: BackgroundTasks,
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
    magic_token = auth_service.generate_magic_token()
    auth_generation = await auth_service.login_challenge_auth_generation(db, email)
    await auth_service.store_login_challenge(
        redis,
        email,
        code,
        magic_token,
        auth_generation=auth_generation,
    )

    base_url = settings.frontend_url.rstrip("/")
    magic_link = f"{base_url}/login/verify#token={magic_token}"

    async def _send_safe(email: str, code: str, magic_link: str) -> None:
        try:
            await send_verification_email(email, code, magic_link)
        except Exception as e:
            logger.error("Failed to send verification email: %s", e, exc_info=True)

    background_tasks.add_task(_send_safe, email, code, magic_link)

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

    current_generation = await auth_service.login_challenge_auth_generation(db, email)
    challenge_generation = await auth_service.consume_verification_code(
        redis,
        email,
        data.code,
        dev_auth_generation=current_generation,
    )
    if challenge_generation is None:
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
    if user.auth_generation != challenge_generation:
        raise BadRequestError("Invalid or expired verification code")
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
    consumed = await auth_service.consume_magic_token(redis, data.token)
    if consumed is None:
        raise BadRequestError("Invalid or expired magic link")
    email, challenge_generation = consumed

    await auth_service.reset_verify_rate_limit(redis, email)
    try:
        auto_approve = await auth_service.validate_email_for_auth(email, db)
    except ValueError:
        auto_approve = False
    user, is_new = await auth_service.get_or_create_user(db, email, auto_approve=auto_approve)
    if user.auth_generation != challenge_generation:
        raise BadRequestError("Invalid or expired magic link")
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

    # Google profile pictures are the only external avatar references we persist.
    # Never allow an arbitrary OIDC URL to become a public redirect target.
    if user.avatar_url and not is_safe_avatar_reference(user.avatar_url, user.id):
        user.avatar_url = None
        updated = True
    if (is_new or not user.avatar_url) and picture and is_trusted_external_avatar_url(picture):
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


@router.post("/setup", response_model=TokenResponse)
@limiter.limit("5/15minutes" if not settings.is_dev else "10000/minute")
async def setup_first_admin(
    request: Request,
    data: SetupIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> TokenResponse:
    """First-run bootstrap: create the initial admin account.

    Only works before the durable installation bootstrap marker is consumed.
    Production additionally requires the out-of-band BOOTSTRAP_TOKEN capability.
    """
    # Serialize the irreversible bootstrap transition with every final-admin mutation.
    await auth_service.acquire_setup_lock(db)
    if await auth_service.installation_bootstrapped(db):
        raise ConflictError("Setup has already been completed")

    auth_service.verify_bootstrap_token(data.bootstrap_token)
    user = await auth_service.create_first_admin(db, data.email, data.password, data.display_name)
    await auth_service.mark_installation_bootstrapped(db)

    logger.info("First admin account created via authenticated setup flow: %s", user.email)
    return _login_response(user, response, is_new=True)


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
        payload = decode_token(token, expected_type="refresh")
    except Exception:
        raise UnauthorizedError("Invalid refresh token")

    user_id = payload.get("sub")
    old_jti = payload.get("jti")
    old_exp = payload.get("exp")
    if not user_id or not old_jti or not old_exp:
        raise UnauthorizedError("Invalid refresh token")

    session_id = payload.get("sid")
    if not isinstance(session_id, str) or not session_id:
        raise UnauthorizedError("Legacy refresh token requires reauthentication")
    if await auth_service.is_session_revoked(redis, session_id):
        raise UnauthorizedError("Session has been revoked")

    user = await get_user_by_id(db, str(user_id))
    if not user:
        raise UnauthorizedError("User not found")
    if not auth_service.token_matches_auth_generation(payload, user):
        raise UnauthorizedError("Credentials require reauthentication")
    if user.role == UserRole.GUEST and not settings.guest_access_enabled:
        raise UnauthorizedError("Guest access is disabled")

    remaining = int(float(old_exp) - datetime.now(UTC).timestamp())
    if remaining <= 0:
        raise UnauthorizedError("Refresh token has expired")

    # This is the single security decision for refresh replay. SET NX in
    # consume_token_once makes the old JTI a one-winner capability even when
    # requests race on different API replicas.
    if not await auth_service.consume_token_once(redis, str(old_jti), remaining):
        await auth_service.revoke_session(
            redis,
            session_id,
            _session_ttl_seconds(user),
        )
        raise UnauthorizedError("Refresh token has already been used")

    # Rotate the browser-native read credential too, so a copied pre-refresh
    # cookie cannot outlive rotation.
    await _blacklist_browser_read_cookie(redis, request)

    successor_session_id = session_id
    if user.role == UserRole.GUEST:
        new_access_token, new_refresh_token, _ = auth_service.issue_tokens(
            user,
            jwt_access_expire_days=GUEST_SESSION_EXPIRE_DAYS,
            jwt_refresh_expire_days=GUEST_SESSION_EXPIRE_DAYS,
            session_id=successor_session_id,
        )
        _set_refresh_cookie(response, new_refresh_token, GUEST_SESSION_EXPIRE_DAYS)
        _set_browser_read_cookie(
            response,
            str(user.id),
            GUEST_SESSION_EXPIRE_DAYS,
            session_id=successor_session_id,
            auth_generation=user.auth_generation,
        )
    else:
        new_access_token, new_refresh_token, _ = auth_service.issue_tokens(
            user,
            session_id=successor_session_id,
        )
        _set_refresh_cookie(response, new_refresh_token)
        _set_browser_read_cookie(
            response,
            str(user.id),
            session_id=successor_session_id,
            auth_generation=user.auth_generation,
        )

    return RefreshResponse(
        access_token=new_access_token,
        user=UserBrief(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            role=user.role.value,
            onboarded=user.onboarded,
            auto_approve=user.auto_approve,
        ),
    )


@router.post("/logout", dependencies=[Depends(require_client_id)])
async def logout(
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    request: Request,
    response: Response,
) -> dict[str, str]:
    payloads: list[dict[str, Any]] = []

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        payload = await _blacklist_token_string(
            redis,
            auth_header[7:],
            expected_type="access",
        )
        if payload:
            payloads.append(payload)

    refresh_payload = await _blacklist_token_string(
        redis,
        request.cookies.get("refresh_token"),
        expected_type="refresh",
    )
    if refresh_payload:
        payloads.append(refresh_payload)

    browser_payload = await _blacklist_browser_read_cookie(redis, request)
    if browser_payload:
        payloads.append(browser_payload)

    session_ids = {str(payload["sid"]) for payload in payloads if payload.get("sid")}
    for session_id in session_ids:
        await auth_service.revoke_session(
            redis,
            session_id,
            _session_ttl_seconds(user),
        )

    response.delete_cookie(
        key="refresh_token",
        path="/api/auth/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        key=BROWSER_READ_COOKIE,
        path="/api/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    return {"message": "Logged out"}
