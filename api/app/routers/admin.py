from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, EmailStr
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.redis import get_redis
from app.dependencies.auth import require_role
from app.models.auth_config import AllowedDomain, AuthConfig
from app.models.dead_letter import DeadLetterJob
from app.models.user import User, UserRole
from app.schemas.common import DetailedHealthResponse, ServiceStatus
from app.services.auth import bust_auth_config_cache, get_full_auth_config

router = APIRouter(prefix="/api/admin", tags=["admin"])


ADMIN_ROLES = (UserRole.BUREAU, UserRole.VIEUX)

AdminUser = Annotated[User, Depends(require_role(UserRole.BUREAU, UserRole.VIEUX))]


# ── User management ───────────────────────────────────────────────────────────


@router.get("/users")
async def admin_list_users(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    role: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> dict:  # type: ignore[type-arg]
    base = select(User)
    if role:
        base = base.where(User.role == role)
    if search:
        pattern = f"%{search}%"
        base = base.where(User.email.ilike(pattern) | User.display_name.ilike(pattern))

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit)
    )
    users = result.scalars().all()
    return {
        "items": [
            {
                "id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "role": u.role.value if u.role else None,
                "onboarded": u.onboarded,
                "created_at": u.created_at.isoformat() if u.created_at is not None else None,  # type: ignore[redundant-expr]
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.patch("/users/{user_id}/role")
async def admin_update_role(
    user_id: uuid.UUID,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    role: str = Query(...),
) -> dict:  # type: ignore[type-arg]
    target = await db.scalar(select(User).where(User.id == user_id))
    if not target:
        raise NotFoundError("User not found")
    try:
        new_role = UserRole(role)
    except ValueError:
        raise BadRequestError(f"Invalid role: {role}")
    if new_role == UserRole.PENDING:
        raise BadRequestError(
            "Cannot manually assign PENDING role; use the approve/reject endpoints"
        )
    target.role = new_role
    await db.flush()
    return {"status": "ok", "role": new_role.value}


@router.delete("/users/{user_id}")
async def admin_delete_user(
    user_id: uuid.UUID,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:  # type: ignore[type-arg]
    from app.services.user import hard_delete_user

    target = await db.scalar(select(User).where(User.id == user_id))
    if not target:
        raise NotFoundError("User not found")
    await hard_delete_user(db, target)
    await db.commit()
    return {"status": "ok"}


@router.post("/users/{user_id}/approve")
async def admin_approve_user(
    user_id: uuid.UUID,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:  # type: ignore[type-arg]
    """Approve a PENDING user — sets their role to STUDENT and notifies them."""
    from app.services.notification import notify_user

    target = await db.scalar(select(User).where(User.id == user_id))
    if not target:
        raise NotFoundError("User not found")
    if target.role != UserRole.PENDING:
        raise BadRequestError("User is not pending approval")

    target.role = UserRole.STUDENT
    await db.flush()

    await notify_user(
        db,
        target.id,
        notification_type="access_approved",
        title="Access approved",
        body="Your account has been approved. Welcome!",
        link="/",
    )
    return {"status": "ok", "role": target.role.value}


@router.post("/users/{user_id}/reject")
async def admin_reject_user(
    user_id: uuid.UUID,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> dict:  # type: ignore[type-arg]
    """Reject and hard-delete a PENDING user."""
    from app.services.user import hard_delete_user

    target = await db.scalar(select(User).where(User.id == user_id))
    if not target:
        raise NotFoundError("User not found")
    if target.role != UserRole.PENDING:
        raise BadRequestError("User is not pending approval")

    await hard_delete_user(db, target)
    return {"status": "ok"}


# ── Dead Letter Queue ─────────────────────────────────────────────────────────


@router.get("/dlq")
async def list_dead_letter_jobs(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    resolved: bool = Query(False),
) -> dict:  # type: ignore[type-arg]
    base = select(DeadLetterJob)
    if not resolved:
        base = base.where(DeadLetterJob.resolved_at.is_(None))

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(DeadLetterJob.created_at.desc()).offset((page - 1) * limit).limit(limit)
    )
    jobs = result.scalars().all()
    return {
        "items": [
            {
                "id": str(j.id),
                "job_name": j.job_name,
                "upload_id": j.upload_id,
                "payload": j.payload,
                "error_detail": j.error_detail,
                "attempts": j.attempts,
                "created_at": j.created_at.isoformat() if j.created_at is not None else None,  # type: ignore[redundant-expr]
                "resolved_at": j.resolved_at.isoformat() if j.resolved_at else None,
            }
            for j in jobs
        ],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.post("/dlq/{job_id}/retry")
async def retry_dead_letter_job(
    job_id: uuid.UUID,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:  # type: ignore[type-arg]
    job = await db.scalar(select(DeadLetterJob).where(DeadLetterJob.id == job_id))
    if not job:
        raise NotFoundError("Dead letter job not found")
    if job.resolved_at is not None:
        raise BadRequestError("Job has already been resolved")

    import app.core.redis as redis_core

    if redis_core.arq_pool is None:
        raise BadRequestError("Background job queue is unavailable")

    payload = job.payload or {}
    await redis_core.arq_pool.enqueue_job(job.job_name, **payload)  # type: ignore[arg-type]

    job.resolved_at = datetime.now(UTC)
    await db.flush()
    return {"status": "ok", "message": "Job re-enqueued"}


@router.post("/dlq/{job_id}/dismiss")
async def dismiss_dead_letter_job(
    job_id: uuid.UUID,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:  # type: ignore[type-arg]
    job = await db.scalar(select(DeadLetterJob).where(DeadLetterJob.id == job_id))
    if not job:
        raise NotFoundError("Dead letter job not found")
    if job.resolved_at is not None:
        raise BadRequestError("Job has already been resolved")

    job.resolved_at = datetime.now(UTC)
    await db.flush()
    return {"status": "ok", "message": "Job dismissed"}


@router.get("/health", response_model=DetailedHealthResponse)
async def get_detailed_health(
    _user: AdminUser,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> DetailedHealthResponse:
    from sqlalchemy import text

    from app.core.meilisearch import meili_admin_client
    from app.core.scanner import MalwareScanner
    from app.models.material import Material, MaterialVersion

    # Fetch full dynamic config for all checks
    config = await get_full_auth_config(db, redis)
    services: dict[str, ServiceStatus] = {}

    # 1. Database Check
    start = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        latency = (time.perf_counter() - start) * 1000
        services["database"] = ServiceStatus(status="healthy", latency_ms=latency)
    except Exception as e:
        services["database"] = ServiceStatus(status="unhealthy", message=str(e))

    # 2. Redis Check
    start = time.perf_counter()
    try:
        await redis.ping()
        latency = (time.perf_counter() - start) * 1000
        services["redis"] = ServiceStatus(status="healthy", latency_ms=latency)
    except Exception as e:
        services["redis"] = ServiceStatus(status="unhealthy", message=str(e))

    # 3. S3 Check (Dynamic)
    start = time.perf_counter()
    try:
        from app.core.storage import get_s3_client

        # Use dynamic config values
        bucket = config.get("s3_bucket") or settings.s3_bucket
        endpoint = config.get("s3_endpoint") or settings.s3_endpoint

        async with get_s3_client() as s3:
            await s3.head_bucket(Bucket=bucket)  # type: ignore[attr-defined]
        latency = (time.perf_counter() - start) * 1000

        # Calculate usage from DB
        usage_bytes = await db.scalar(select(func.sum(MaterialVersion.file_size))) or 0

        services["storage"] = ServiceStatus(
            status="healthy",
            latency_ms=latency,
            metadata={
                "bucket": bucket,
                "usage_bytes": usage_bytes,
                "max_storage_bytes": (  # type: ignore[operator]
                    config.get("max_storage_gb")
                    if config.get("max_storage_gb") is not None
                    else settings.max_storage_gb
                )
                * 1024
                * 1024
                * 1024,
                "endpoint": endpoint,
                "ssl": config.get("s3_use_ssl", settings.s3_use_ssl),
            },
        )
    except Exception as e:
        services["storage"] = ServiceStatus(status="unhealthy", message=str(e))

    # 4. Email (SMTP) Check (Dynamic)
    start = time.perf_counter()
    try:
        import aiosmtplib

        host = config.get("smtp_host") or settings.smtp_host
        port = config.get("smtp_port") or settings.smtp_port

        if host or config.get("smtp_ip") or settings.smtp_ip:
            # Quick ping to SMTP port
            # Use IP if provided, otherwise hostname
            connect_host = config.get("smtp_ip") or settings.smtp_ip or host
            # Health check is a reachability probe only — disable cert validation
            # so connecting via IP address doesn't trigger SSL hostname mismatch.
            smtp = aiosmtplib.SMTP(
                hostname=connect_host, port=port, timeout=2, validate_certs=False
            )
            # connect() does not accept server_hostname — just open the connection
            await smtp.connect()
            await smtp.quit()
            latency = (time.perf_counter() - start) * 1000
            services["email"] = ServiceStatus(
                status="healthy",
                latency_ms=latency,
                metadata={
                    "host": host,
                    "ip": config.get("smtp_ip") or settings.smtp_ip,
                    "port": port,
                    "user": config.get("smtp_user") or settings.smtp_user,
                },
            )
        else:
            services["email"] = ServiceStatus(status="degraded", message="SMTP not configured")
    except Exception as e:
        services["email"] = ServiceStatus(status="unhealthy", message=str(e))

    # 5. MeiliSearch Check
    start = time.perf_counter()
    try:
        health = await meili_admin_client.health()
        latency = (time.perf_counter() - start) * 1000
        services["search"] = ServiceStatus(
            status="healthy" if health.status == "available" else "degraded", latency_ms=latency
        )
    except Exception as e:
        services["search"] = ServiceStatus(status="unhealthy", message=str(e))

    # 6. ARQ Workers
    start = time.perf_counter()
    try:
        # Check heartbeats and pending jobs for the three worker queues
        queues = ["arq:queue", "upload-fast", "upload-slow"]
        heartbeats = {}
        queue_counts = {}
        for q in queues:
            hc = await redis.get(f"{q}:health-check")
            heartbeats[q] = hc is not None
            # ARQ uses a Redis list for the queue
            count = await redis.llen(q)  # type: ignore[misc]
            queue_counts[q] = count

        active_queues = [q for q, alive in heartbeats.items() if alive]
        latency = (time.perf_counter() - start) * 1000

        services["workers"] = ServiceStatus(
            status="healthy"
            if len(active_queues) == len(queues)
            else "unhealthy"
            if not active_queues
            else "degraded",
            latency_ms=latency,
            message=None if active_queues else "No active heartbeats detected from worker pool",
            metadata={
                "active_queues": active_queues,
                "missing_queues": [q for q, alive in heartbeats.items() if not alive],
                "queue_counts": queue_counts,
            },
        )
    except Exception as e:
        services["workers"] = ServiceStatus(status="unhealthy", message=str(e))

    # 7. Malware Scanner
    start = time.perf_counter()
    try:
        scanner: MalwareScanner = getattr(request.app.state, "scanner", None)  # type: ignore[assignment]
        latency = (time.perf_counter() - start) * 1000

        is_ready = scanner is not None and scanner.initialized  # type: ignore[redundant-expr]
        pending_scans = await db.scalar(
            select(func.count())
            .select_from(MaterialVersion)
            .where(MaterialVersion.virus_scan_result == "pending")
        )

        services["scanner"] = ServiceStatus(
            status="healthy" if is_ready else "degraded",
            latency_ms=latency,
            message=None if is_ready else "Scanner not initialized",
            metadata={
                "yara_enabled": is_ready,
                "malwarebazaar_enabled": bool(
                    config.get("malwarebazaar_api_key") or settings.malwarebazaar_api_key
                ),
                "pending_scans": pending_scans,
            },
        )
    except Exception as e:
        services["scanner"] = ServiceStatus(status="unhealthy", message=str(e))

    # Global Metrics
    user_count = await db.scalar(select(func.count()).select_from(User))
    material_count = await db.scalar(select(func.count()).select_from(Material))
    pending_dlq = await db.scalar(
        select(func.count()).select_from(DeadLetterJob).where(DeadLetterJob.resolved_at.is_(None))
    )

    overall_status = (
        "healthy" if all(s.status == "healthy" for s in services.values()) else "degraded"
    )
    if any(s.status == "unhealthy" for s in services.values()):
        overall_status = "unhealthy"

    return DetailedHealthResponse(
        status=overall_status,
        timestamp=datetime.now(UTC).isoformat(),
        services=services,
        metrics={
            "total_users": user_count,
            "total_materials": material_count,
            "pending_jobs": pending_dlq,
            "max_upload_size_mb": config.get("max_file_size_mb") or settings.max_file_size_mb,
            "google_auth_enabled": config.get("google_oauth_enabled", False),
        },
    )


# ── Auth configuration ────────────────────────────────────────────────────────


class AuthConfigPatch(BaseModel):
    totp_enabled: bool | None = None
    google_oauth_enabled: bool | None = None
    google_client_id: str | None = None
    classic_auth_enabled: bool | None = None
    allow_all_domains: bool | None = None
    auto_approve_all_domains: bool | None = None
    guest_access_enabled: bool | None = None
    jwt_access_expire_days: int | None = None
    jwt_refresh_expire_days: int | None = None
    smtp_host: str | None = None
    smtp_ip: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool | None = None
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str | None = None
    s3_public_endpoint: str | None = None
    s3_region: str | None = None
    s3_use_ssl: bool | None = None
    max_storage_gb: int | None = None

    max_file_size_mb: int | None = None
    max_image_size_mb: int | None = None
    max_audio_size_mb: int | None = None
    max_video_size_mb: int | None = None
    max_document_size_mb: int | None = None
    max_office_size_mb: int | None = None
    max_text_size_mb: int | None = None
    pdf_quality: int | None = None
    video_compression_profile: str | None = None
    thumbnail_quality: int | None = None
    thumbnail_size_px: int | None = None
    allowed_extensions: str | None = None
    allowed_mime_types: str | None = None

    site_name: str | None = None
    site_name_style: str | None = None
    site_description: str | None = None
    site_logo_url: str | None = None
    site_favicon_url: str | None = None
    primary_color: str | None = None
    footer_text: str | None = None
    footer_logo_url: str | None = None
    organization_url: str | None = None
    og_image_url: str | None = None
    bg_watermark_url: str | None = None
    bg_watermark_opacity_light: float | None = None
    bg_watermark_opacity_dark: float | None = None
    legal_name: str | None = None
    legal_address: str | None = None
    legal_siret: str | None = None
    contact_email: str | None = None
    dpo_email: str | None = None
    dpo_address: str | None = None
    data_transfers: str | None = None
    legal_version: str | None = None


class DomainCreate(BaseModel):
    domain: str
    auto_approve: bool = True


class DomainPatch(BaseModel):
    auto_approve: bool | None = None


_REDACTED_FIELDS = frozenset({"smtp_password", "s3_access_key", "s3_secret_key"})


def _redact_config_for_api(config: dict[str, Any]) -> dict[str, Any]:
    """Replace secret values with boolean presence flags for API responses.

    Secrets must never be returned to clients — even admin clients — because
    they appear in browser history, logs, and proxies. The UI only needs to
    know whether a value is set in order to render the appropriate placeholder.
    """
    out = dict(config)
    for field in _REDACTED_FIELDS:
        out[f"{field}_set"] = bool(out.pop(field, None))
    return out


@router.get("/auth-config")
async def get_auth_config(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    return _redact_config_for_api(await get_full_auth_config(db, redis))


@router.patch("/auth-config")
async def patch_auth_config(
    body: Annotated[AuthConfigPatch, Body()],
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    config_row = await db.scalar(select(AuthConfig))
    if config_row is None:
        config_row = AuthConfig()
        db.add(config_row)

    update_data = body.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if isinstance(value, str):
            value = value.strip() if value else None
        setattr(config_row, field, value)

    config_row.updated_at = datetime.now(UTC)
    await db.flush()
    await bust_auth_config_cache(redis)
    return _redact_config_for_api(await get_full_auth_config(db, redis))


@router.get("/auth-config/domains")
async def list_domains(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:  # type: ignore[type-arg]
    result = await db.execute(select(AllowedDomain).order_by(AllowedDomain.domain))
    domains = result.scalars().all()
    return [{"id": str(d.id), "domain": d.domain, "auto_approve": d.auto_approve} for d in domains]


@router.post("/auth-config/domains", status_code=201)
async def add_domain(
    body: Annotated[DomainCreate, Body()],
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    domain = body.domain.strip().lstrip("@").lower()
    if not domain:
        raise BadRequestError("Domain cannot be empty")

    existing = await db.scalar(select(AllowedDomain).where(AllowedDomain.domain == domain))
    if existing:
        raise ConflictError(f"Domain '{domain}' already exists")

    row = AllowedDomain(domain=domain, auto_approve=body.auto_approve)
    db.add(row)
    await db.flush()
    await bust_auth_config_cache(redis)
    return {"id": str(row.id), "domain": row.domain, "auto_approve": row.auto_approve}


@router.patch("/auth-config/domains/{domain_id}")
async def update_domain(
    domain_id: uuid.UUID,
    body: Annotated[DomainPatch, Body()],
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    row = await db.scalar(select(AllowedDomain).where(AllowedDomain.id == domain_id))
    if not row:
        raise NotFoundError("Domain not found")

    if body.auto_approve is not None:
        row.auto_approve = body.auto_approve

    await db.flush()
    await bust_auth_config_cache(redis)
    return {"id": str(row.id), "domain": row.domain, "auto_approve": row.auto_approve}


@router.delete("/auth-config/domains/{domain_id}")
async def delete_domain(
    domain_id: uuid.UUID,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    row = await db.scalar(select(AllowedDomain).where(AllowedDomain.id == domain_id))
    if not row:
        raise NotFoundError("Domain not found")

    await db.delete(row)
    await db.flush()
    await bust_auth_config_cache(redis)
    return {"status": "ok"}


class TestEmailIn(BaseModel):
    email: EmailStr


@router.post("/auth-config/test-email")
async def admin_test_email(
    body: Annotated[TestEmailIn, Body()],
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:  # type: ignore[type-arg]
    from app.core.email import send_email

    config = await db.scalar(select(AuthConfig))

    sitename = (config.site_name if config is not None else None) or settings.site_name

    subject = f"{sitename} - Test Email"
    body_text = f"This is a test email from {sitename}. Current time: {datetime.now(UTC)}"

    try:
        await send_email(body.email, subject, body_text, config=config)
    except Exception as e:
        raise BadRequestError(f"Failed to send test email: {str(e)}")

    return {"status": "ok", "message": "Test email sent"}


_LOGO_ALLOWED_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/svg+xml", "image/webp", "image/gif"}
)
_FAVICON_ALLOWED_TYPES = frozenset(
    {"image/png", "image/x-icon", "image/vnd.microsoft.icon", "image/svg+xml"}
)
_IMAGE_ALLOWED_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml"}
)
_BRANDING_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


_WEBP_SKIP_TYPES = frozenset({"image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon"})


async def _upload_branding_asset(
    file: UploadFile,
    key_prefix: str,
    allowed_types: frozenset[str],
    db: AsyncSession,
    redis: Redis,  # type: ignore[type-arg]
    config_field: str,
) -> dict:  # type: ignore[type-arg]
    import io

    from PIL import Image

    from app.core.storage import get_public_url, upload_file

    content_type = file.content_type or "application/octet-stream"
    if content_type not in allowed_types:
        raise BadRequestError(
            f"Invalid file type '{content_type}'. Allowed: {', '.join(sorted(allowed_types))}"
        )

    data = await file.read()
    if len(data) > _BRANDING_MAX_BYTES:
        raise BadRequestError("File too large. Maximum 5 MB.")
    if not data:
        raise BadRequestError("Empty file.")

    # Convert raster images to lossless WebP; skip SVG/ICO (not raster).
    if content_type not in _WEBP_SKIP_TYPES:
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", lossless=True, quality=100)
        data = buf.getvalue()
        content_type = "image/webp"

    ext = (
        "webp"
        if content_type == "image/webp"
        else (
            (file.filename or f"{key_prefix}.bin").rsplit(".", 1)[-1].lower()
            if "." in (file.filename or "")
            else "bin"
        )
    )
    key = f"branding/{key_prefix}.{ext}"

    await upload_file(data, key, content_type=content_type, content_disposition="inline")
    url = await get_public_url(key)

    config_row = await db.scalar(select(AuthConfig))
    if config_row is None:
        config_row = AuthConfig()
        db.add(config_row)
    setattr(config_row, config_field, url)
    config_row.updated_at = datetime.now(UTC)
    await db.flush()
    await bust_auth_config_cache(redis)

    return {"url": url}


@router.post("/auth-config/upload-logo")
async def upload_logo(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    file: UploadFile = File(...),
) -> dict:  # type: ignore[type-arg]
    return await _upload_branding_asset(
        file, "logo", _LOGO_ALLOWED_TYPES, db, redis, "site_logo_url"
    )


@router.post("/auth-config/upload-favicon")
async def upload_favicon(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    file: UploadFile = File(...),
) -> dict:  # type: ignore[type-arg]
    return await _upload_branding_asset(
        file, "favicon", _FAVICON_ALLOWED_TYPES, db, redis, "site_favicon_url"
    )


@router.post("/auth-config/upload-og-image")
async def upload_og_image(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    file: UploadFile = File(...),
) -> dict:  # type: ignore[type-arg]
    return await _upload_branding_asset(
        file, "og-image", _IMAGE_ALLOWED_TYPES, db, redis, "og_image_url"
    )


@router.post("/auth-config/upload-bg-watermark")
async def upload_bg_watermark(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    file: UploadFile = File(...),
) -> dict:  # type: ignore[type-arg]
    return await _upload_branding_asset(
        file, "bg-watermark", _IMAGE_ALLOWED_TYPES, db, redis, "bg_watermark_url"
    )


@router.post("/auth-config/upload-footer-logo")
async def upload_footer_logo(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    file: UploadFile = File(...),
) -> dict:  # type: ignore[type-arg]
    return await _upload_branding_asset(
        file, "footer-logo", _LOGO_ALLOWED_TYPES, db, redis, "footer_logo_url"
    )
