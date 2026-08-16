import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.core.common.exceptions import AppError
from app.core.events.limiter import limiter
from app.core.http.body_limit import RequestBodyLimitMiddleware
from app.core.observability.telemetry import instrument_fastapi, setup_telemetry
from app.routers.admin import router as admin_router
from app.routers.admin_backup import router as admin_backup_router
from app.routers.admin_operations import router as admin_operations_router
from app.routers.admin_storage import router as admin_storage_router
from app.routers.annotations import (
    annotations_router,
    material_annotations_router,
)
from app.routers.auth import router as auth_router
from app.routers.browse import router as browse_router
from app.routers.collections import router as collections_router
from app.routers.comments import router as comments_router
from app.routers.directories import router as directories_router
from app.routers.eurooffice import router as eurooffice_router
from app.routers.events import router as events_router
from app.routers.flags import router as flags_router
from app.routers.home import router as home_router
from app.routers.materials import router as materials_router
from app.routers.moderator import router as moderator_router
from app.routers.notifications import router as notifications_router
from app.routers.og import router as og_router
from app.routers.pr_comments import router as pr_comments_router
from app.routers.pull_requests import router as pull_requests_router
from app.routers.qcm import router as qcm_router
from app.routers.search import router as search_router
from app.routers.tus import router as tus_router
from app.routers.upload import router as upload_api_router
from app.routers.users import router as users_router
from app.schemas.common import HealthResponse

logger = logging.getLogger(__name__)


def _s3_csp_domain() -> str:
    """Extract the bare host[:port] from the S3 public endpoint for CSP header use."""
    from urllib.parse import urlparse

    ep = settings.s3_public_endpoint or ""
    return urlparse(ep).netloc or ep


_S3_CSP_DOMAIN = _s3_csp_domain()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("API starting up")

    # Gunicorn preloads this module before forking. Exporter threads and Redis
    # monkeypatching therefore belong in each worker's lifespan, not import time.
    setup_telemetry()

    from app.core.database.redis import close_arq_pool, init_arq_pool
    from app.core.events.meilisearch import setup_meilisearch
    from app.core.security.scanner import MalwareScanner
    from app.core.storage.facade import close_s3_client, init_s3_client

    # Soft-fail: degraded but non-critical services
    try:
        await setup_meilisearch()
    except Exception as e:
        logger.error("MeiliSearch setup failed (search degraded): %s", e)

    try:
        await init_arq_pool()
    except Exception as e:
        logger.error("ARQ pool setup failed (background jobs degraded): %s", e)

    # SSE fan-out: deliver real-time events across API replicas and from workers.
    from app.core.events.sse import start_sse_pubsub

    try:
        await start_sse_pubsub(subscribe=True)
    except Exception as e:
        logger.error("SSE pub/sub setup failed (live updates degraded): %s", e)

    # Ensure backup directory exists
    from pathlib import Path

    Path(settings.backup_dir).mkdir(parents=True, exist_ok=True)

    # Hard-fail: storage and scanner are required for safe operation
    await init_s3_client()

    scanner = MalwareScanner()
    scanner.initialize()
    app.state.scanner = scanner

    yield
    logger.info("API shutting down")
    from app.core.events.sse import stop_sse_pubsub

    await stop_sse_pubsub()
    await scanner.close()
    await close_arq_pool()
    await close_s3_client()
    from app.core.database.redis import close_redis_client

    await close_redis_client()

    from app.core.events.meilisearch import meili_admin_client, meili_search_client

    await meili_admin_client.aclose()
    if meili_search_client is not None and meili_search_client is not meili_admin_client:
        await meili_search_client.aclose()

    from app.core.observability.telemetry import shutdown_telemetry

    shutdown_telemetry()


app = FastAPI(
    title=f"{settings.site_name} API",
    description="Collaborative course materials platform",
    version="0.1.0",
    docs_url="/api/docs" if settings.is_dev else None,
    openapi_url="/api/openapi.json" if settings.is_dev else None,
    lifespan=lifespan,
)

# Instrument before Starlette builds its middleware stack. Calling the
# instrumentor from inside lifespan startup is too late for the first stack.
instrument_fastapi(app)

# ── Security Headers (S23) ───────────────────────────────────────────────────


@app.middleware("http")
async def add_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)

    s3_domain = _S3_CSP_DOMAIN

    # Build dynamic CSP
    connect_src = (
        f"connect-src 'self' {settings.frontend_url} "
        f"https://accounts.google.com/gsi/ https://unpkg.com https://cdn.jsdelivr.net "
    )
    if s3_domain:
        connect_src += f"https://{s3_domain} "
    connect_src += "https://*.r2.cloudflarestorage.com;"

    img_src = "img-src 'self' data: blob: https:;"
    if s3_domain:
        img_src = f"img-src 'self' data: blob: https: https://{s3_domain};"

    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client https://unpkg.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style https://cdn.jsdelivr.net; "
        + img_src
        + " "
        "font-src 'self'; " + connect_src + " "
        "frame-src https://accounts.google.com/gsi/; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )

    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


app.state.limiter = limiter


async def rate_limit_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests"},
    )


async def app_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, AppError):
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.code,
            "error_message": exc.detail,
            "detail": exc.detail,  # backward compat
        },
    )


app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_exception_handler(AppError, app_error_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=settings.cors_headers_list,
)

# Honor forwarding headers only when the immediate peer is an explicitly
# configured reverse proxy. This keeps IP-based limits meaningful on port 8000.
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_hosts=settings.trusted_proxy_hosts_list,
)

import re

from app.core.common.batch_upload_limits import BATCH_REQUEST_BODY_LIMIT_BYTES

# These endpoints otherwise parse attacker-controlled JSON/multipart bodies in
# memory before their route-level validators run. Keep the transport ceiling
# close to the domain limits and enforce it even when nginx is bypassed.
app.add_middleware(
    RequestBodyLimitMiddleware,
    # Ordinary JSON mutations should never inherit the proxy's large file-upload
    # allowance. Explicit large-body routes override this small default.
    default_limit=1 * 1024 * 1024,
    path_limits={
        "/api/qcm/stage": 20 * 1024 * 1024,
        "/api/qcm/parse-moodle": 11 * 1024 * 1024,
        "/api/upload/batch-zip": BATCH_REQUEST_BODY_LIMIT_BYTES,
        "/api/admin/backup/restore/upload": 500 * 1024 * 1024,
    },
    pattern_limits=[
        (
            "POST",
            re.compile(r"/api/materials/[^/]+/text-content"),
            10 * 1024 * 1024,
        ),
        (
            "POST",
            re.compile(r"/api/upload/?"),
            (settings.max_file_size_mb + 2) * 1024 * 1024,
        ),
        (
            "PATCH",
            re.compile(r"/api/upload/tus/[^/]+"),
            settings.tus_chunk_max_bytes,
        ),
    ],
)


@app.middleware("http")
async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed = time.perf_counter() - start
    logger.info(
        "%s %s %d %.3fs",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    from app.core.database.redis import arq_pool, redis_client

    redis_ok = False
    try:
        await redis_client.ping()
        redis_ok = True
    except Exception:
        pass

    return HealthResponse(
        status="ok" if redis_ok and arq_pool else "degraded",
        details={
            "redis": "connected" if redis_ok else "disconnected",
            "arq_pool": "initialized" if arq_pool else "not_initialized",
        },
    )


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Prometheus metrics scrape endpoint.

    Protected by a bearer token when ``METRICS_TOKEN`` is set in config.
    Leave unset (default) for unauthenticated scraping inside private networks.
    """
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from app.core.observability.metrics import REGISTRY

    if settings.metrics_token:
        import hmac

        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token, settings.metrics_token):
            return Response(status_code=403, content="Forbidden")

    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


app.include_router(admin_router)
app.include_router(admin_backup_router)
app.include_router(admin_operations_router)
app.include_router(admin_storage_router)
app.include_router(moderator_router)
app.include_router(annotations_router)
app.include_router(auth_router)
app.include_router(browse_router)
app.include_router(comments_router)
app.include_router(collections_router)
app.include_router(directories_router)
app.include_router(flags_router)
app.include_router(material_annotations_router)
app.include_router(materials_router)
app.include_router(notifications_router)
app.include_router(og_router)
app.include_router(pr_comments_router)
app.include_router(pull_requests_router)
app.include_router(search_router)
app.include_router(eurooffice_router)
app.include_router(events_router)
app.include_router(tus_router)
app.include_router(upload_api_router)
app.include_router(home_router)
app.include_router(qcm_router)
app.include_router(users_router)

if settings.is_dev:
    try:
        from sqladmin import Admin, ModelView
        from sqladmin.authentication import AuthenticationBackend
        from starlette.requests import Request
        from starlette.responses import RedirectResponse

        from app.core.database.database import engine

        class SimpleAdminAuth(AuthenticationBackend):
            async def login(self, request: Request) -> bool:
                form = await request.form()
                username, password = form.get("username"), form.get("password")
                # Basic dev-only auth. Use metrics_token if set, else allow anyone.
                # (S22) Recommends basic auth even in dev.
                expected = settings.metrics_token or "dev-admin-secret"
                if username == "admin" and password == expected:
                    request.session.update({"token": password})
                    return True
                return False

            async def logout(self, request: Request) -> bool:
                request.session.clear()
                return True

            async def authenticate(self, request: Request) -> RedirectResponse | bool:
                token = request.session.get("token")
                expected = settings.metrics_token or "dev-admin-secret"
                if not token or token != expected:
                    return RedirectResponse(request.url_for("admin:login"))
                return True

        authentication_backend = SimpleAdminAuth(secret_key=settings.secret_key.get_secret_value())
        admin = Admin(app, engine, authentication_backend=authentication_backend)

        from app.models.annotation import Annotation
        from app.models.comment import Comment
        from app.models.directory import Directory
        from app.models.flag import Flag
        from app.models.material import Material, MaterialVersion
        from app.models.notification import Notification
        from app.models.pull_request import PRComment, PullRequest
        from app.models.tag import Tag
        from app.models.user import User
        from app.models.view_history import ViewHistory

        class UserAdmin(ModelView, model=User):  # type: ignore[call-arg]
            column_list = [User.id, User.email, User.display_name, User.role, User.onboarded]

        class DirectoryAdmin(ModelView, model=Directory):  # type: ignore[call-arg]
            column_list = [Directory.id, Directory.name, Directory.slug, Directory.type]

        class MaterialAdmin(ModelView, model=Material):  # type: ignore[call-arg]
            column_list = [Material.id, Material.title, Material.type, Material.slug]

        class MaterialVersionAdmin(ModelView, model=MaterialVersion):  # type: ignore[call-arg]
            column_list = [
                MaterialVersion.id,
                MaterialVersion.material_id,
                MaterialVersion.version_number,
            ]

        class TagAdmin(ModelView, model=Tag):  # type: ignore[call-arg]
            column_list = [Tag.id, Tag.name, Tag.category]

        class PullRequestAdmin(ModelView, model=PullRequest):  # type: ignore[call-arg]
            column_list = [PullRequest.id, PullRequest.title, PullRequest.type, PullRequest.status]

        class PRCommentAdmin(ModelView, model=PRComment):  # type: ignore[call-arg]
            column_list = [PRComment.id, PRComment.pr_id, PRComment.body]

        class CommentAdmin(ModelView, model=Comment):  # type: ignore[call-arg]
            column_list = [Comment.id, Comment.target_type, Comment.target_id, Comment.body]

        class AnnotationAdmin(ModelView, model=Annotation):  # type: ignore[call-arg]
            column_list = [Annotation.id, Annotation.material_id, Annotation.body]

        class FlagAdmin(ModelView, model=Flag):  # type: ignore[call-arg]
            column_list = [Flag.id, Flag.target_type, Flag.reason, Flag.status]

        class NotificationAdmin(ModelView, model=Notification):  # type: ignore[call-arg]
            column_list = [
                Notification.id,
                Notification.user_id,
                Notification.type,
                Notification.title,
            ]

        class ViewHistoryAdmin(ModelView, model=ViewHistory):  # type: ignore[call-arg]
            column_list = [ViewHistory.id, ViewHistory.user_id, ViewHistory.material_id]

        admin.add_view(UserAdmin)
        admin.add_view(DirectoryAdmin)
        admin.add_view(MaterialAdmin)
        admin.add_view(MaterialVersionAdmin)
        admin.add_view(TagAdmin)
        admin.add_view(PullRequestAdmin)
        admin.add_view(PRCommentAdmin)
        admin.add_view(CommentAdmin)
        admin.add_view(AnnotationAdmin)
        admin.add_view(FlagAdmin)
        admin.add_view(NotificationAdmin)
        admin.add_view(ViewHistoryAdmin)
    except ImportError:
        pass
