from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from passlib.context import CryptContext
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.common.exceptions import (
    ConflictError,
    ForbiddenError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.core.security.security import create_access_token, create_refresh_token
from app.models.auth_config import AllowedDomain
from app.models.installation import InstallationState
from app.models.user import User, UserRole

CODE_TTL_SECONDS = 900
RATE_LIMIT_TTL_SECONDS = 900
RATE_LIMIT_MAX = 3
VERIFY_RATE_LIMIT_MAX = 5
VERIFY_RATE_LIMIT_TTL_SECONDS = 600

# Redis scripts are deliberately small and self-identifying. Challenge issuance
# and compare/delete decisions happen server-side so concurrent requests cannot
# pass a read-before-write race. Marker comments are mirrored by FakeRedis.
_STORE_LOGIN_CHALLENGE_LUA = r"""
-- auth_store_login_challenge_v3
local previous_magic = redis.call("GET", KEYS[2])
if previous_magic then
    redis.call("DEL", "auth:magic:" .. previous_magic)
end
redis.call("SET", KEYS[1], ARGV[1], "EX", ARGV[5])
redis.call("SET", KEYS[3], ARGV[2], "EX", ARGV[5])
redis.call("SET", KEYS[2], ARGV[3], "EX", ARGV[5])
redis.call("SET", KEYS[4], ARGV[4], "EX", ARGV[5])
return 1
"""

_VERIFY_CODE_LUA = r"""
-- auth_verify_code_v2
local stored = redis.call("GET", KEYS[1])
if not stored or stored ~= ARGV[1] then
    return {0}
end
local generation = redis.call("GET", KEYS[3]) or "0"
local magic_token = redis.call("GET", KEYS[2])
redis.call("DEL", KEYS[1])
redis.call("DEL", KEYS[3])
if magic_token then
    redis.call("DEL", "auth:magic:" .. magic_token)
    redis.call("DEL", KEYS[2])
end
return {1, generation}
"""

_VERIFY_MAGIC_LUA = r"""
-- auth_verify_magic_v3
local email = redis.call("GET", KEYS[1])
if not email or email ~= ARGV[1] then
    return {0}
end

local current_ref = redis.call("GET", KEYS[2])
if not current_ref or current_ref ~= ARGV[2] then
    redis.call("DEL", KEYS[1])
    return {0}
end

local generation = redis.call("GET", KEYS[4]) or "0"
redis.call("DEL", KEYS[1])
redis.call("DEL", KEYS[2])
redis.call("DEL", KEYS[3])
redis.call("DEL", KEYS[4])
return {1, generation}
"""

# Fallback used when DB has no AllowedDomain rows and ALLOWED_DOMAINS env is unset.
# Empty by default — operators configure allowed domains via the admin UI,
# the ALLOWED_DOMAINS env var, or enable allow_all_domains.
_FALLBACK_DOMAINS: list[dict[str, Any]] = []

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
    3. _FALLBACK_DOMAINS — empty by default (no domains baked in).
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


async def login_challenge_auth_generation(db: AsyncSession, email: str) -> int:
    """Return the credential generation a newly issued login challenge must bind to.

    A missing user is generation 0. Legacy challenges created before generation
    binding also decode as generation 0, preserving upgrade compatibility while
    making them fail closed after any recovery increments the account generation.
    """
    generation = await db.scalar(select(User.auth_generation).where(User.email == email))
    return int(generation or 0)


async def store_login_challenge(
    redis: Redis,  # type: ignore[type-arg]
    email: str,
    code: str,
    magic_token: str,
    *,
    auth_generation: int,
) -> None:
    """Atomically supersede and generation-bind the login challenge for *email*."""
    script = redis.register_script(_STORE_LOGIN_CHALLENGE_LUA)
    result = await script(
        keys=[
            f"auth:code:{email}",
            f"auth:magic_ref:{email}",
            f"auth:magic:{magic_token}",
            f"auth:challenge_gen:{email}",
        ],
        args=[code, email, magic_token, auth_generation, CODE_TTL_SECONDS],
        client=redis,
    )
    if int(result) != 1:
        raise RuntimeError("Redis failed to store login challenge atomically")


# Low-level compatibility helpers. Production login issuance must use
# store_login_challenge so code + magic link share one generation binding.
# Challenges written through these legacy helpers intentionally decode as
# generation 0 and therefore cannot cross a recovery boundary.
async def store_code(redis: Redis, email: str, code: str) -> None:  # type: ignore[type-arg]
    await redis.delete(f"auth:challenge_gen:{email}")
    await redis.setex(f"auth:code:{email}", CODE_TTL_SECONDS, code)


async def store_magic_token(redis: Redis, email: str, token: str) -> None:  # type: ignore[type-arg]
    previous = await redis.get(f"auth:magic_ref:{email}")
    if isinstance(previous, bytes):
        previous = previous.decode()
    if previous:
        await redis.delete(f"auth:magic:{previous}")
    await redis.delete(f"auth:challenge_gen:{email}")
    await redis.setex(f"auth:magic:{token}", CODE_TTL_SECONDS, email)
    await redis.setex(f"auth:magic_ref:{email}", CODE_TTL_SECONDS, token)


def _challenge_generation_from_script(result: object) -> int | None:
    if not isinstance(result, (list, tuple)) or not result:
        return None
    if int(result[0]) != 1 or len(result) < 2:
        return None
    raw_generation = result[1]
    if isinstance(raw_generation, bytes):
        raw_generation = raw_generation.decode()
    try:
        generation = int(raw_generation)
    except (TypeError, ValueError):
        return None
    return generation if generation >= 0 else None


async def consume_verification_code(
    redis: Redis,  # type: ignore[type-arg]
    email: str,
    code: str,
    *,
    dev_auth_generation: int = 0,
) -> int | None:
    """Consume a code once and return the generation captured at issuance."""
    if settings.is_dev and code in {"00000000", "AAAAAAAA"}:
        return dev_auth_generation

    code_key = f"auth:code:{email}"
    script = redis.register_script(_VERIFY_CODE_LUA)
    result = await script(
        keys=[
            code_key,
            f"auth:magic_ref:{email}",
            f"auth:challenge_gen:{email}",
        ],
        args=[code],
        client=redis,
    )
    return _challenge_generation_from_script(result)


async def verify_code(redis: Redis, email: str, code: str) -> bool:  # type: ignore[type-arg]
    """Compatibility wrapper for callers that only need single-use validity."""
    return await consume_verification_code(redis, email, code) is not None


async def consume_magic_token(redis: Redis, token: str) -> tuple[str, int] | None:  # type: ignore[type-arg]
    """Consume a magic token once and return ``(email, issuance_generation)``."""
    token_key = f"auth:magic:{token}"
    email = await redis.get(token_key)
    if not email:
        return None

    if isinstance(email, bytes):
        email = email.decode()

    script = redis.register_script(_VERIFY_MAGIC_LUA)
    result = await script(
        keys=[
            token_key,
            f"auth:magic_ref:{email}",
            f"auth:code:{email}",
            f"auth:challenge_gen:{email}",
        ],
        args=[email, token],
        client=redis,
    )
    generation = _challenge_generation_from_script(result)
    return (email, generation) if generation is not None else None


async def verify_magic_token(redis: Redis, token: str) -> str | None:  # type: ignore[type-arg]
    """Compatibility wrapper returning only the challenge email."""
    consumed = await consume_magic_token(redis, token)
    return consumed[0] if consumed is not None else None


async def _consume_rate_limit(
    redis: Redis,  # type: ignore[type-arg]
    key: str,
    *,
    maximum: int,
    ttl_seconds: int,
) -> bool:
    """Atomically consume one fixed-window attempt across all API replicas."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, ttl_seconds, nx=True)
        results = await pipe.execute()
    return int(results[0]) <= maximum


async def check_rate_limit(redis: Redis, email: str) -> bool:  # type: ignore[type-arg]
    if settings.is_dev:
        return True
    return await _consume_rate_limit(
        redis,
        f"auth:rate:{email}",
        maximum=RATE_LIMIT_MAX,
        ttl_seconds=RATE_LIMIT_TTL_SECONDS,
    )


async def check_verify_rate_limit(redis: Redis, email: str) -> bool:  # type: ignore[type-arg]
    if settings.is_dev:
        return True

    return await _consume_rate_limit(
        redis,
        f"auth:verify_rate:{email}",
        maximum=VERIFY_RATE_LIMIT_MAX,
        ttl_seconds=VERIFY_RATE_LIMIT_TTL_SECONDS,
    )


async def increment_verify_rate_limit(redis: Redis, email: str) -> None:  # type: ignore[type-arg]
    """Compatibility no-op: the attempt is consumed before verification."""
    return None


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
    *,
    session_id: str | None = None,
) -> tuple[str, str, str]:
    session_id = session_id or str(uuid4())
    access_token, jti = create_access_token(
        user_id=str(user.id),
        role=user.role.value,
        email=user.email,
        expire_days=jwt_access_expire_days,
        session_id=session_id,
        auth_generation=user.auth_generation,
    )
    refresh_token = create_refresh_token(
        user_id=str(user.id),
        expire_days=jwt_refresh_expire_days,
        session_id=session_id,
        auth_generation=user.auth_generation,
    )
    return access_token, refresh_token, jti


async def blacklist_token(redis: Redis, jti: str, ttl_seconds: int) -> None:  # type: ignore[type-arg]
    if ttl_seconds > 0:
        await redis.setex(f"auth:blacklist:{jti}", ttl_seconds, "1")


async def consume_token_once(redis: Redis, jti: str, ttl_seconds: int) -> bool:  # type: ignore[type-arg]
    """Atomically consume a JTI.

    The consumed marker deliberately shares the blacklist namespace so every
    normal token-validation path immediately observes the token as revoked.
    """
    if ttl_seconds <= 0:
        return False
    result = await redis.set(
        f"auth:blacklist:{jti}",
        "1",
        ex=ttl_seconds,
        nx=True,
    )
    return bool(result)


async def is_token_blacklisted(redis: Redis, jti: str) -> bool:  # type: ignore[type-arg]
    result = await redis.get(f"auth:blacklist:{jti}")
    return result is not None


async def revoke_session(redis: Redis, session_id: str, ttl_seconds: int) -> None:  # type: ignore[type-arg]
    if ttl_seconds > 0:
        await redis.setex(f"auth:session_revoked:{session_id}", ttl_seconds, "1")


async def is_session_revoked(redis: Redis, session_id: str) -> bool:  # type: ignore[type-arg]
    return await redis.get(f"auth:session_revoked:{session_id}") is not None


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
# every mutation that can create or remove the administrative authority boundary
# (spells "WIKI" — value is irrelevant, just stable).
_SETUP_LOCK_KEY = 0x57494B49
_ADMIN_ROLES = (UserRole.BUREAU, UserRole.VIEUX)


async def acquire_setup_lock(db: AsyncSession) -> None:
    """Serialize bootstrap and final-admin mutations in PostgreSQL.

    The same transaction-level advisory lock protects both the one-time bootstrap
    transition and every operation that could remove an administrator. This makes
    two concurrent demotions/deletions observe each other's committed result.
    No-op on non-Postgres backends used by hermetic tests.
    """
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK_KEY})


def bootstrap_token_required() -> bool:
    return settings.environment == "production" or settings.bootstrap_token is not None


def verify_bootstrap_token(provided: str | None) -> None:
    """Require an out-of-band operator capability for HTTP bootstrap."""
    configured = settings.bootstrap_token
    if configured is None:
        if settings.environment == "production":
            raise ServiceUnavailableError(
                "Production bootstrap is disabled until BOOTSTRAP_TOKEN is configured.",
                code="BOOTSTRAP_TOKEN_NOT_CONFIGURED",
            )
        return

    expected = configured.get_secret_value()
    if provided is None or not secrets.compare_digest(provided, expected):
        raise UnauthorizedError(
            "Invalid bootstrap token",
            code="INVALID_BOOTSTRAP_TOKEN",
        )


async def installation_bootstrapped(db: AsyncSession) -> bool:
    """True once the one-way installation marker has been committed."""
    marker = await db.scalar(select(InstallationState.id).where(InstallationState.id == 1))
    return marker is not None


async def mark_installation_bootstrapped(db: AsyncSession) -> None:
    """Consume HTTP bootstrap permanently inside the caller's transaction."""
    if await installation_bootstrapped(db):
        return
    db.add(InstallationState(id=1))
    await db.flush()


async def admin_exists(db: AsyncSession) -> bool:
    """True if at least one live administrative account exists."""
    result = await db.execute(
        select(User.id).where(User.role.in_(_ADMIN_ROLES), User.deleted_at.is_(None)).limit(1)
    )
    return result.scalar_one_or_none() is not None


def token_matches_auth_generation(payload: dict[str, Any], user: User) -> bool:
    """Return whether a JWT belongs to the user's current credential generation.

    Tokens minted before this field existed carry no ``gen`` claim; generation zero
    deliberately accepts those legacy credentials until a security-sensitive recovery
    increments the user generation.
    """
    token_generation = payload.get("gen", 0)
    return (
        isinstance(token_generation, int)
        and not isinstance(token_generation, bool)
        and token_generation == user.auth_generation
    )


async def lock_user_for_authority_change(
    db: AsyncSession, user_id: object, *, include_deleted: bool = False
) -> User | None:
    """Acquire the authority lock, then return a freshly row-locked user.

    ``populate_existing`` is required because AsyncSession identity maps can otherwise
    hand a caller an ORM object whose role predates the authority lock.
    """
    await acquire_setup_lock(db)
    statement = (
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if include_deleted:
        statement = statement.execution_options(include_deleted=True)
    return await db.scalar(statement)


async def lock_admin_authority_change(
    db: AsyncSession,
    actor_id: object,
    target_id: object,
    *,
    expected_auth_generation: int,
) -> tuple[User, User | None]:
    """Serialize an admin user-management mutation and revalidate its actor.

    FastAPI role dependencies authorize before route execution. A request can then
    wait for the authority advisory lock while another administrator revokes the
    actor. Therefore the dependency's ORM object is admission evidence only: after
    acquiring the shared lock we freshly row-lock the actor, require that they are
    still a live administrator, and require the credential generation observed at
    admission to remain current. Only then is the target loaded under the same lock.

    The transaction-level advisory lock is held through request commit, so another
    authority mutation cannot demote/delete the actor between this revalidation and
    the caller's mutation.
    """
    await acquire_setup_lock(db)

    actor = await db.scalar(
        select(User)
        .where(User.id == actor_id, User.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        actor is None
        or actor.role not in _ADMIN_ROLES
        or actor.auth_generation != expected_auth_generation
    ):
        raise ForbiddenError(
            "Administrative authority was revoked; reauthenticate before retrying.",
            code="ADMIN_AUTHORITY_REVOKED",
        )

    if actor.id == target_id:
        return actor, actor

    target = await db.scalar(
        select(User)
        .where(User.id == target_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return actor, target


async def ensure_admin_removal_safe(db: AsyncSession, user_id: object) -> None:
    """Lock and authoritatively validate deletion/demotion of a user.

    The authority lock is acquired unconditionally and held through the caller's
    transaction. The target role/deletion state is fetched from PostgreSQL only
    *after* that lock, with a row lock and without trusting an ORM instance loaded
    earlier in the request. Promotions use the same authority lock, so a target
    cannot become an administrator between this check and the caller's mutation.
    Soft-deleted administrators are not live authority and therefore do not block
    retention/GDPR purges.
    """
    await acquire_setup_lock(db)
    current = (
        await db.execute(
            select(User.role, User.deleted_at)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(include_deleted=True)
        )
    ).one_or_none()
    if current is None:
        return

    current_role, deleted_at = current
    if current_role not in _ADMIN_ROLES or deleted_at is not None:
        return

    remaining = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.role.in_(_ADMIN_ROLES),
            User.deleted_at.is_(None),
            User.id != user_id,
        )
    )
    if not remaining:
        raise ConflictError(
            "Cannot remove the final administrator. Use the offline admin recovery "
            "command if recovery is required.",
            code="LAST_ADMIN_REQUIRED",
        )


async def recover_admin_account(db: AsyncSession, email: str, password: str) -> tuple[User, bool]:
    """Restore administrative authority while invalidating all older credentials.

    This service is intended for the offline CLI. Existing users keep their identity
    and data, but their credential generation is incremented before authority is
    restored so stolen pre-recovery access/refresh/browser tokens fail immediately
    once services restart. HTTP bootstrap remains permanently consumed.
    """
    await acquire_setup_lock(db)
    user = await db.scalar(
        select(User)
        .where(User.email == email)
        .with_for_update()
        .execution_options(include_deleted=True, populate_existing=True)
    )
    now = datetime.now(UTC)
    created = user is None
    if user is None:
        user = User(
            email=email,
            role=UserRole.BUREAU,
            password_hash=get_password_hash(password),
            onboarded=True,
            gdpr_consent=True,
            gdpr_consent_at=now,
            auto_approve=True,
        )
        db.add(user)
    else:
        user.auth_generation += 1
        user.deleted_at = None
        user.role = UserRole.BUREAU
        user.password_hash = get_password_hash(password)
        user.onboarded = True
        user.gdpr_consent = True
        user.gdpr_consent_at = user.gdpr_consent_at or now
        user.auto_approve = True

    await mark_installation_bootstrapped(db)
    await db.flush()
    return user, created


async def create_first_admin(
    db: AsyncSession, email: str, password: str, display_name: str | None
) -> User:
    """Create the bootstrap admin account. Caller must hold the setup lock."""
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
