from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any

from passlib.context import CryptContext
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token, create_refresh_token
from app.models.auth_config import AllowedDomain, AuthConfig
from app.models.user import User, UserRole

CODE_TTL_SECONDS = 900
RATE_LIMIT_TTL_SECONDS = 900
RATE_LIMIT_MAX = 3
VERIFY_RATE_LIMIT_MAX = 5
VERIFY_RATE_LIMIT_TTL_SECONDS = 600

AUTH_CONFIG_CACHE_KEY = "auth:full_config"
AUTH_CONFIG_CACHE_TTL = 60

# Fields that must never be stored in the Redis cache.  Secrets are always
# re-hydrated from the DB (or env settings) on every call — accepting one
# extra indexed SELECT per cache-hit in exchange for keeping credentials off
# the Redis keyspace.
_REDIS_SECRET_FIELDS = frozenset({"smtp_password", "s3_access_key", "s3_secret_key"})

# Hardcoded fallback used when DB has no AllowedDomain rows (pre-migration or test envs).
_FALLBACK_DOMAINS = [
    {"domain": "telecom-sudparis.eu", "auto_approve": True},
    {"domain": "imt-bs.eu", "auto_approve": True},
]

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _extract_secrets(config_row: AuthConfig | None) -> dict[str, Any]:
    """Return the secret fields from a DB row, falling back to env settings."""
    return {
        "smtp_password": (
            config_row.smtp_password
            if config_row and config_row.smtp_password is not None
            else settings.smtp_password
        ),
        "s3_access_key": (
            config_row.s3_access_key
            if config_row and config_row.s3_access_key is not None
            else settings.s3_access_key
        ),
        "s3_secret_key": (
            config_row.s3_secret_key
            if config_row and config_row.s3_secret_key is not None
            else settings.s3_secret_key
        ),
    }


async def get_full_auth_config(db: AsyncSession, redis: Redis) -> dict[str, Any]:  # type: ignore[type-arg]
    """Return auth config from Redis cache, falling back to DB.

    Secrets (smtp_password, s3_access_key, s3_secret_key) are never stored in
    Redis — they are always re-loaded from the DB row on every call to prevent
    credential exposure if the Redis keyspace is read by an unauthorized party.
    """
    cached = await redis.get(AUTH_CONFIG_CACHE_KEY)
    if cached:
        raw = cached if isinstance(cached, str) else cached.decode()
        result = json.loads(raw)
        # Re-hydrate secrets from DB (never cached in Redis)
        config_row = await db.scalar(select(AuthConfig))
        result.update(_extract_secrets(config_row))
        return result  # type: ignore[no-any-return]

    config_row = await db.scalar(select(AuthConfig))
    domain_rows = list((await db.execute(select(AllowedDomain))).scalars().all())

    if config_row is None:
        result: dict[str, Any] = {  # type: ignore[no-redef]
            "totp_enabled": True,
            "google_oauth_enabled": False,
            "google_client_id": None,
            "allow_all_domains": False,
            "auto_approve_all_domains": False,
            "classic_auth_enabled": True,
            "jwt_access_expire_days": settings.jwt_access_token_expire_days,
            "jwt_refresh_expire_days": settings.jwt_refresh_token_expire_days,
            "smtp_host": settings.smtp_host,
            "smtp_port": settings.smtp_port,
            "smtp_user": settings.smtp_user,
            "smtp_password": settings.smtp_password,
            "smtp_ip": settings.smtp_ip,
            "smtp_from": settings.smtp_from,
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
            "allowed_extensions": None,
            "allowed_mime_types": None,
            "site_name": settings.site_name,
            "site_name_style": None,
            "site_description": settings.site_description,
            "site_logo_url": settings.site_logo_url,
            "site_favicon_url": settings.site_favicon_url,
            "primary_color": settings.primary_color,
            "footer_text": settings.footer_text,
            "footer_logo_url": None,
            "organization_url": settings.organization_url,
            "og_image_url": None,
            "bg_watermark_url": None,
            "bg_watermark_opacity_light": None,
            "bg_watermark_opacity_dark": None,
            "legal_name": settings.legal_name,
            "legal_address": settings.legal_address,
            "contact_email": settings.contact_email,
            "dpo_email": settings.dpo_email,
            "dpo_address": settings.dpo_address,
            "data_transfers": settings.data_transfers,
            "legal_version": settings.legal_version,
            "domains": _FALLBACK_DOMAINS,
        }
    else:
        domains = [
            {"id": str(d.id), "domain": d.domain, "auto_approve": d.auto_approve}
            for d in domain_rows
        ]
        result = {
            "totp_enabled": config_row.totp_enabled,
            "google_oauth_enabled": config_row.google_oauth_enabled,
            "google_client_id": config_row.google_client_id,
            "classic_auth_enabled": config_row.classic_auth_enabled,
            "allow_all_domains": config_row.allow_all_domains,
            "auto_approve_all_domains": config_row.auto_approve_all_domains,
            "jwt_access_expire_days": config_row.jwt_access_expire_days
            if config_row.jwt_access_expire_days is not None  # type: ignore[redundant-expr]
            else settings.jwt_access_token_expire_days,
            "jwt_refresh_expire_days": config_row.jwt_refresh_expire_days
            if config_row.jwt_refresh_expire_days is not None  # type: ignore[redundant-expr]
            else settings.jwt_refresh_token_expire_days,
            "smtp_host": config_row.smtp_host
            if config_row.smtp_host is not None
            else settings.smtp_host,
            "smtp_ip": config_row.smtp_ip if config_row.smtp_ip is not None else settings.smtp_ip,
            "smtp_port": config_row.smtp_port
            if config_row.smtp_port is not None
            else settings.smtp_port,
            "smtp_user": config_row.smtp_user
            if config_row.smtp_user is not None
            else settings.smtp_user,
            "smtp_password": config_row.smtp_password
            if config_row.smtp_password is not None
            else settings.smtp_password,
            "smtp_from": config_row.smtp_from
            if config_row.smtp_from is not None
            else settings.smtp_from,
            "smtp_use_tls": config_row.smtp_use_tls
            if config_row.smtp_use_tls is not None  # type: ignore[redundant-expr]
            else settings.smtp_use_tls,
            "s3_endpoint": config_row.s3_endpoint
            if config_row.s3_endpoint is not None
            else settings.s3_endpoint,
            "s3_access_key": config_row.s3_access_key
            if config_row.s3_access_key is not None
            else settings.s3_access_key,
            "s3_secret_key": config_row.s3_secret_key
            if config_row.s3_secret_key is not None
            else settings.s3_secret_key,
            "s3_bucket": config_row.s3_bucket
            if config_row.s3_bucket is not None
            else settings.s3_bucket,
            "s3_public_endpoint": config_row.s3_public_endpoint
            if config_row.s3_public_endpoint is not None
            else settings.s3_public_endpoint,
            "s3_region": config_row.s3_region
            if config_row.s3_region is not None
            else settings.s3_region,
            "s3_use_ssl": config_row.s3_use_ssl
            if config_row.s3_use_ssl is not None  # type: ignore[redundant-expr]
            else settings.s3_use_ssl,
            "max_storage_gb": config_row.max_storage_gb
            if config_row.max_storage_gb is not None
            else settings.max_storage_gb,
            "max_file_size_mb": config_row.max_file_size_mb
            if config_row.max_file_size_mb is not None
            else settings.max_file_size_mb,
            "max_image_size_mb": config_row.max_image_size_mb
            if config_row.max_image_size_mb is not None
            else settings.max_image_size_mb,
            "max_audio_size_mb": config_row.max_audio_size_mb
            if config_row.max_audio_size_mb is not None
            else settings.max_audio_size_mb,
            "max_video_size_mb": config_row.max_video_size_mb
            if config_row.max_video_size_mb is not None
            else settings.max_video_size_mb,
            "max_document_size_mb": config_row.max_document_size_mb
            if config_row.max_document_size_mb is not None
            else settings.max_document_size_mb,
            "max_office_size_mb": config_row.max_office_size_mb
            if config_row.max_office_size_mb is not None
            else settings.max_office_size_mb,
            "max_text_size_mb": config_row.max_text_size_mb
            if config_row.max_text_size_mb is not None
            else settings.max_text_size_mb,
            "pdf_quality": config_row.pdf_quality
            if config_row.pdf_quality is not None
            else settings.pdf_quality,
            "video_compression_profile": config_row.video_compression_profile
            if config_row.video_compression_profile is not None
            else settings.video_compression_profile,
            "thumbnail_quality": config_row.thumbnail_quality
            if config_row.thumbnail_quality is not None
            else 85,
            "thumbnail_size_px": config_row.thumbnail_size_px
            if config_row.thumbnail_size_px is not None
            else 640,
            "allowed_extensions": config_row.allowed_extensions,
            "allowed_mime_types": config_row.allowed_mime_types,
            "site_name": config_row.site_name
            if config_row.site_name is not None
            else settings.site_name,
            "site_name_style": config_row.site_name_style,
            "site_description": config_row.site_description
            if config_row.site_description is not None
            else settings.site_description,
            "site_logo_url": config_row.site_logo_url
            if config_row.site_logo_url is not None
            else settings.site_logo_url,
            "site_favicon_url": config_row.site_favicon_url
            if config_row.site_favicon_url is not None
            else settings.site_favicon_url,
            "primary_color": config_row.primary_color
            if config_row.primary_color is not None
            else settings.primary_color,
            "footer_text": config_row.footer_text
            if config_row.footer_text is not None
            else settings.footer_text,
            "organization_url": config_row.organization_url
            if config_row.organization_url is not None
            else settings.organization_url,
            "footer_logo_url": config_row.footer_logo_url,
            "og_image_url": config_row.og_image_url,
            "bg_watermark_url": config_row.bg_watermark_url,
            "bg_watermark_opacity_light": config_row.bg_watermark_opacity_light,
            "bg_watermark_opacity_dark": config_row.bg_watermark_opacity_dark,
            "legal_name": config_row.legal_name
            if config_row.legal_name is not None
            else settings.legal_name,
            "legal_address": config_row.legal_address
            if config_row.legal_address is not None
            else settings.legal_address,
            "legal_siret": config_row.legal_siret
            if config_row.legal_siret is not None
            else settings.legal_siret,
            "contact_email": config_row.contact_email
            if config_row.contact_email is not None
            else settings.contact_email,
            "dpo_email": config_row.dpo_email
            if config_row.dpo_email is not None
            else settings.dpo_email,
            "dpo_address": config_row.dpo_address
            if config_row.dpo_address is not None
            else settings.dpo_address,
            "data_transfers": config_row.data_transfers
            if config_row.data_transfers is not None
            else settings.data_transfers,
            "legal_version": config_row.legal_version
            if config_row.legal_version is not None
            else settings.legal_version,
            "domains": domains if domains else _FALLBACK_DOMAINS,
        }

    cacheable = {k: v for k, v in result.items() if k not in _REDIS_SECRET_FIELDS}
    await redis.setex(AUTH_CONFIG_CACHE_KEY, AUTH_CONFIG_CACHE_TTL, json.dumps(cacheable))
    return result  # type: ignore[no-any-return]


async def get_auth_config(db: AsyncSession) -> AuthConfig | None:
    return await db.scalar(select(AuthConfig))


async def bust_auth_config_cache(redis: Redis) -> None:  # type: ignore[type-arg]
    await redis.delete(AUTH_CONFIG_CACHE_KEY)


async def validate_email_for_auth(email: str, db: AsyncSession, redis: Redis) -> bool:  # type: ignore[type-arg]
    """Validate email domain against DB config.

    Returns the domain's ``auto_approve`` flag for new-user role assignment.
    Raises ``ValueError`` if the email domain is not allowed and
    ``allow_all_domains`` is False.

    When ``allow_all_domains`` is True any domain passes, but only domains
    explicitly listed with ``auto_approve=True`` skip the manual review step;
    unlisted domains still receive ``PENDING`` status (``auto_approve=False``).
    """
    config = await get_full_auth_config(db, redis)

    domain = email.split("@")[1] if "@" in email else ""
    for d in config["domains"]:
        if d["domain"] == domain:
            return bool(d["auto_approve"])

    if config.get("allow_all_domains"):
        return bool(config.get("auto_approve_all_domains", False))

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
