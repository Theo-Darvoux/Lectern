from __future__ import annotations

import base64
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, EmailStr
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.redis import get_redis
from app.dependencies.auth import require_role
from app.models.auth_config import AllowedDomain
from app.models.dead_letter import DeadLetterJob
from app.models.user import User, UserRole
from app.schemas.common import DetailedHealthResponse, ServiceStatus
from app.services.auth import get_full_auth_config
from app.services.notification import notify_user
from app.services.user import hard_delete_user

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
    cursor: Annotated[str | None, Query()] = None,
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

    if cursor:
        try:
            decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
            created_at_str, id_str = decoded.split(",", 1)
            cursor_dt = datetime.fromisoformat(created_at_str)
            cursor_id = uuid.UUID(id_str)
            base = base.where(
                or_(
                    User.created_at < cursor_dt,
                    and_(User.created_at == cursor_dt, User.id < cursor_id),
                )
            )
        except Exception:
            raise BadRequestError("Invalid cursor")

    result = await db.execute(
        base.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)
    )
    users = list(result.scalars().all())

    has_more = len(users) > limit
    if has_more:
        users = users[:limit]

    next_cursor: str | None = None
    if has_more and users:
        last = users[-1]
        raw = f"{last.created_at.isoformat()},{last.id}"
        next_cursor = base64.urlsafe_b64encode(raw.encode()).decode()

    return {
        "items": [
            {
                "id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "role": u.role.value if u.role else None,
                "onboarded": u.onboarded,
                "created_at": u.created_at.isoformat(),
            }
            for u in users
        ],
        "total": total,
        "next_cursor": next_cursor,
        "has_more": has_more,
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
                "created_at": j.created_at.isoformat(),
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

        bucket = settings.s3_bucket
        endpoint = settings.s3_endpoint

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
                "max_storage_bytes": settings.max_storage_gb * 1024 * 1024 * 1024,  # type: ignore[operator]
                "endpoint": endpoint,
                "ssl": settings.s3_use_ssl,
            },
        )
    except Exception as e:
        services["storage"] = ServiceStatus(status="unhealthy", message=str(e))

    # 4. Email (SMTP) Check (Dynamic)
    start = time.perf_counter()
    try:
        import aiosmtplib

        host = settings.smtp_host
        port = settings.smtp_port

        if host or settings.smtp_ip:
            # Quick ping to SMTP port
            # Use IP if provided, otherwise hostname
            connect_host = settings.smtp_ip or host
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
                    "ip": settings.smtp_ip,
                    "port": port,
                    "user": settings.smtp_user,
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
                "malwarebazaar_enabled": bool(settings.malwarebazaar_api_key),
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
            "max_upload_size_mb": settings.max_file_size_mb,
            "google_auth_enabled": settings.google_oauth_enabled,
        },
    )


# ── Auth configuration ────────────────────────────────────────────────────────


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
) -> dict:  # type: ignore[type-arg]
    return _redact_config_for_api(await get_full_auth_config(db))


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
    return {"id": str(row.id), "domain": row.domain, "auto_approve": row.auto_approve}


@router.patch("/auth-config/domains/{domain_id}")
async def update_domain(
    domain_id: uuid.UUID,
    body: Annotated[DomainPatch, Body()],
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:  # type: ignore[type-arg]
    row = await db.scalar(select(AllowedDomain).where(AllowedDomain.id == domain_id))
    if not row:
        raise NotFoundError("Domain not found")

    if body.auto_approve is not None:
        row.auto_approve = body.auto_approve

    await db.flush()
    return {"id": str(row.id), "domain": row.domain, "auto_approve": row.auto_approve}


@router.delete("/auth-config/domains/{domain_id}")
async def delete_domain(
    domain_id: uuid.UUID,
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:  # type: ignore[type-arg]
    row = await db.scalar(select(AllowedDomain).where(AllowedDomain.id == domain_id))
    if not row:
        raise NotFoundError("Domain not found")

    await db.delete(row)
    await db.flush()
    return {"status": "ok"}


class TestEmailIn(BaseModel):
    email: EmailStr


@router.post("/auth-config/test-email")
async def admin_test_email(
    body: Annotated[TestEmailIn, Body()],
    _user: AdminUser,
) -> dict:  # type: ignore[type-arg]
    from app.core.email import send_email

    sitename = settings.site_name
    subject = f"{sitename} - Test Email"
    body_text = f"This is a test email from {sitename}. Current time: {datetime.now(UTC)}"

    try:
        await send_email(body.email, subject, body_text)
    except Exception as e:
        raise BadRequestError(f"Failed to send test email: {str(e)}")

    return {"status": "ok", "message": "Test email sent"}
