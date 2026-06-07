from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from passlib.context import CryptContext
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token, create_refresh_token
from app.models.auth_config import AllowedDomain
from app.models.user import User, UserRole

CODE_TTL_SECONDS = 900
RATE_LIMIT_TTL_SECONDS = 900
RATE_LIMIT_MAX = 3
VERIFY_RATE_LIMIT_MAX = 5
VERIFY_RATE_LIMIT_TTL_SECONDS = 600

# Hardcoded fallback used when DB has no AllowedDomain rows and ALLOWED_DOMAINS env is unset.
_FALLBACK_DOMAINS = [
    {"domain": "telecom-sudparis.eu", "auto_approve": True},
    {"domain": "imt-bs.eu", "auto_approve": True},
]

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _parse_allowed_domains(raw: str) -> list[dict[str, Any]]:
    """Parse ALLOWED_DOMAINS env string into a list of domain policy dicts.

    Format: "domain1:auto,domain2:manual" — "auto" sets auto_approve=True,
    "manual" sets auto_approve=False. Default when mode is omitted is auto.
    """
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            domain, mode = part.rsplit(":", 1)
            auto_approve = mode.strip().lower() != "manual"
        else:
            domain = part
            auto_approve = True
        result.append({"domain": domain.strip(), "auto_approve": auto_approve})
    return result


async def get_allowed_domains(db: AsyncSession) -> tuple[list[dict[str, Any]], bool]:
    """Resolve the allowed domain list from env override, DB rows, or hardcoded fallback.

    Returns ``(domains, domains_from_env)``.
    Domain resolution order:
    1. ALLOWED_DOMAINS env var — read-only; the UI shows an "overridden by .env" banner.
    2. allowed_domains DB table — editable via admin CRUD.
    3. _FALLBACK_DOMAINS — hardcoded default for fresh installs.
    """
    if settings.allowed_domains:
        return _parse_allowed_domains(settings.allowed_domains), True
    domain_rows = list((await db.execute(select(AllowedDomain))).scalars().all())
    domains = (
        [{"id": str(d.id), "domain": d.domain, "auto_approve": d.auto_approve} for d in domain_rows]
        if domain_rows
        else _FALLBACK_DOMAINS
    )
    return domains, False


async def get_full_auth_config(db: AsyncSession) -> dict[str, Any]:
    """Return the full admin-facing config dict derived from environment settings.

    Only call this from the admin GET /config endpoint. All other callers should
    read ``settings`` directly — this function exists solely to produce the
    complete config dump in one pass for admin inspection.
    """
    domains, domains_from_env = await get_allowed_domains(db)

    return {
        "totp_enabled": settings.totp_enabled,
        "google_oauth_enabled": settings.google_oauth_enabled,
        "google_client_id": settings.google_client_id,
        "classic_auth_enabled": settings.classic_auth_enabled,
        "allow_all_domains": settings.allow_all_domains,
        "auto_approve_all_domains": settings.auto_approve_all_domains,
        "guest_access_enabled": settings.guest_access_enabled,
        "jwt_access_expire_days": settings.jwt_access_token_expire_days,
        "jwt_refresh_expire_days": settings.jwt_refresh_token_expire_days,
        "smtp_host": settings.smtp_host,
        "smtp_ip": settings.smtp_ip,
        "smtp_port": settings.smtp_port,
        "smtp_user": settings.smtp_user,
        "smtp_password": settings.smtp_password,
        "smtp_from": settings.smtp_from,
        "smtp_sender_name": settings.smtp_sender_name,
        "smtp_avatar_url": settings.smtp_avatar_url,
        "smtp_use_tls": settings.smtp_use_tls,
        "s3_endpoint": settings.s3_endpoint,
        "s3_access_key": settings.s3_access_key,
        "s3_secret_key": settings.s3_secret_key,
        "s3_bucket": settings.s3_bucket,
        "s3_public_endpoint": settings.s3_public_endpoint,
        "s3_region": settings.s3_region,
        "s3_use_ssl": settings.s3_use_ssl,
        "max_storage_gb": settings.max_storage_gb,
        "max_file_size_mb": settings.max_file_size_mb,
        "max_image_size_mb": settings.max_image_size_mb,
        "max_audio_size_mb": settings.max_audio_size_mb,
        "max_video_size_mb": settings.max_video_size_mb,
        "max_document_size_mb": settings.max_document_size_mb,
        "max_office_size_mb": settings.max_office_size_mb,
        "max_text_size_mb": settings.max_text_size_mb,
        "pdf_quality": settings.pdf_quality,
        "video_compression_profile": settings.video_compression_profile,
        "thumbnail_quality": settings.thumbnail_quality,
        "thumbnail_size_px": settings.thumbnail_size_px,
        "allowed_extensions": settings.allowed_extensions,
        "allowed_mime_types": settings.allowed_mime_types,
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
        "domains": domains,
        "domains_from_env": domains_from_env,
    }


async def validate_email_for_auth(email: str, db: AsyncSession) -> bool:
    """Validate email domain against DB config.

    Returns the domain's ``auto_approve`` flag for new-user role assignment.
    Raises ``ValueError`` if the email domain is not allowed and
    ``allow_all_domains`` is False.

    When ``allow_all_domains`` is True any domain passes, but only domains
    explicitly listed with ``auto_approve=True`` skip the manual review step;
    unlisted domains still receive ``PENDING`` status (``auto_approve=False``).
    """
    domains, _ = await get_allowed_domains(db)

    domain = email.split("@")[1] if "@" in email else ""
    for d in domains:
        if d["domain"] == domain:
            return bool(d["auto_approve"])

    if settings.allow_all_domains:
        return bool(settings.auto_approve_all_domains)

    raise ValueError(f"Email domain @{domain} is not allowed")


def validate_email_format(email: str) -> str:
    """Synchronous format-only validation (no domain policy check)."""
    email = email.strip().lower()
    if "+" in email:
        raise ValueError("Email aliases with '+' are not allowed")
    return email


def generate_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def generate_magic_token() -> str:
    return secrets.token_urlsafe(48)


async def store_code(redis: Redis, email: str, code: str) -> None:  # type: ignore[type-arg]
    await redis.setex(f"auth:code:{email}", CODE_TTL_SECONDS, code)


async def store_magic_token(redis: Redis, email: str, token: str) -> None:  # type: ignore[type-arg]
    await redis.setex(f"auth:magic:{token}", CODE_TTL_SECONDS, email)
    await redis.setex(f"auth:magic_ref:{email}", CODE_TTL_SECONDS, token)


async def verify_code(redis: Redis, email: str, code: str) -> bool:  # type: ignore[type-arg]
    if settings.is_dev and code in {"00000000", "AAAAAAAA"}:
        return True

    stored = await redis.get(f"auth:code:{email}")
    if stored and stored == code:
        await redis.delete(f"auth:code:{email}")
        magic_token = await redis.get(f"auth:magic_ref:{email}")
        if magic_token:
            await redis.delete(f"auth:magic:{magic_token}")
            await redis.delete(f"auth:magic_ref:{email}")
        return True
    return False


async def verify_magic_token(redis: Redis, token: str) -> str | None:  # type: ignore[type-arg]
    email = await redis.get(f"auth:magic:{token}")
    if not email:
        return None

    if isinstance(email, bytes):
        email = email.decode()

    await redis.delete(f"auth:magic:{token}")
    await redis.delete(f"auth:magic_ref:{email}")
    await redis.delete(f"auth:code:{email}")
    return email


async def check_rate_limit(redis: Redis, email: str) -> bool:  # type: ignore[type-arg]
    if settings.is_dev:
        return True

    key = f"auth:rate:{email}"
    count = await redis.get(key)
    if count and int(count) >= RATE_LIMIT_MAX:
        return False
    pipe = redis.pipeline()
    await pipe.incr(key)
    await pipe.expire(key, RATE_LIMIT_TTL_SECONDS)
    await pipe.execute()
    return True


async def check_verify_rate_limit(redis: Redis, email: str) -> bool:  # type: ignore[type-arg]
    if settings.is_dev:
        return True

    key = f"auth:verify_rate:{email}"
    count = await redis.get(key)
    return not (count and int(count) >= VERIFY_RATE_LIMIT_MAX)


async def increment_verify_rate_limit(redis: Redis, email: str) -> None:  # type: ignore[type-arg]
    if settings.is_dev:
        return
    key = f"auth:verify_rate:{email}"
    pipe = redis.pipeline()
    await pipe.incr(key)
    await pipe.expire(key, VERIFY_RATE_LIMIT_TTL_SECONDS)
    await pipe.execute()


async def reset_verify_rate_limit(redis: Redis, email: str) -> None:  # type: ignore[type-arg]
    await redis.delete(f"auth:verify_rate:{email}")


async def get_or_create_user(
    db: AsyncSession, email: str, auto_approve: bool = False
) -> tuple[User, bool]:
    """Return (user, is_new).

    ``auto_approve`` must be the result of a prior ``validate_email_for_auth``
    call.  Callers are responsible for domain validation — this function only
    maps the pre-validated flag to a role (STUDENT vs. PENDING) for new users.
    Existing users are returned unchanged.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is not None:
        user.last_login_at = datetime.now(UTC)
        return user, False

    role = UserRole.STUDENT if auto_approve else UserRole.PENDING
    user = User(email=email, role=role)
    db.add(user)
    await db.flush()
    return user, True


async def get_guest_user(db: AsyncSession) -> User | None:
    """Return the single shared guest identity seeded by migration 012."""
    return await db.scalar(select(User).where(User.role == UserRole.GUEST))


def issue_tokens(
    user: User,
    jwt_access_expire_days: int | None = None,
    jwt_refresh_expire_days: int | None = None,
) -> tuple[str, str, str]:
    access_token, jti = create_access_token(
        user_id=str(user.id),
        role=user.role.value,
        email=user.email,
        expire_days=jwt_access_expire_days,
    )
    refresh_token = create_refresh_token(user_id=str(user.id), expire_days=jwt_refresh_expire_days)
    return access_token, refresh_token, jti


async def blacklist_token(redis: Redis, jti: str, ttl_seconds: int) -> None:  # type: ignore[type-arg]
    await redis.setex(f"auth:blacklist:{jti}", ttl_seconds, "1")


async def is_token_blacklisted(redis: Redis, jti: str) -> bool:  # type: ignore[type-arg]
    result = await redis.get(f"auth:blacklist:{jti}")
    return result is not None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)  # type: ignore[no-any-return]


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)  # type: ignore[no-any-return]


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# Arbitrary fixed key for the transaction-level advisory lock that serializes
# the first-admin bootstrap (spells "WIKI" — value is irrelevant, just stable).
_SETUP_LOCK_KEY = 0x57494B49


async def acquire_setup_lock(db: AsyncSession) -> None:
    """Serialize concurrent first-admin setup attempts.

    Without this, two simultaneous requests on a fresh instance could both pass
    the ``admin_exists`` check and create two admins. A Postgres transaction-level
    advisory lock makes the check-then-create atomic; it's released at commit.
    No-op on non-Postgres backends (e.g. SQLite in tests), where the test suite
    is single-threaded anyway.
    """
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK_KEY})


async def admin_exists(db: AsyncSession) -> bool:
    """True if at least one (non-deleted) admin account exists.

    Drives the first-run setup flow: while this is ``False`` the instance has no
    way in, so ``POST /api/auth/setup`` is allowed to bootstrap the first admin.
    """
    admin_roles = [UserRole.BUREAU, UserRole.VIEUX]
    result = await db.execute(
        select(User.id).where(User.role.in_(admin_roles), User.deleted_at.is_(None)).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def create_first_admin(
    db: AsyncSession, email: str, password: str, display_name: str | None
) -> User:
    """Create the bootstrap admin account. Caller must ensure no admin exists yet."""
    user = User(
        email=email,
        display_name=display_name,
        role=UserRole.BUREAU,
        password_hash=get_password_hash(password),
        onboarded=True,
        gdpr_consent=True,
        gdpr_consent_at=datetime.now(UTC),
        auto_approve=True,
        last_login_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    return user
