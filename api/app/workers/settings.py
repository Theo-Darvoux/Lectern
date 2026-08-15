from typing import Any

from arq.cron import cron

from app.config import settings
from app.core.database.redis import build_redis_settings, close_arq_pool, init_arq_pool
from app.workers.check_bazaar import check_bazaar
from app.workers.cleanup_uploads import cleanup_uploads
from app.workers.gdpr_cleanup import gdpr_cleanup
from app.workers.index_content import (
    delete_indexed_item,
    index_directories_batch,
    index_directory,
    index_material,
    index_materials_batch,
    reconcile_search_documents,
)
from app.workers.outbox import dispatch_outbox
from app.workers.process_upload import process_upload
from app.workers.process_upload_post_scan import process_upload_post_scan
from app.workers.reconcile_multipart import reconcile_multipart_uploads
from app.workers.recover_cas_storage import recover_cas_storage_mutations
from app.workers.storage_ops import (
    add_cas_references,
    delete_storage_objects,
    release_cas_references,
    release_storage_reservations,
    release_upload_quota,
)
from app.workers.view_reset import reset_14d_views, reset_daily_views
from app.workers.webhook_dispatch import dispatch_webhook
from app.workers.year_rollover import year_rollover


async def startup(ctx: dict[str, Any]) -> None:
    import logging
    import shutil

    logger = logging.getLogger(__name__)

    if not shutil.which("bwrap"):
        raise RuntimeError(
            "bwrap (bubblewrap) is required but not found. Install it: apt install bubblewrap"
        )

    try:
        import oletools.olevba as _olevba

        _ = _olevba
    except ImportError:
        raise RuntimeError(
            "oletools is required for OLE2 macro detection. Install: pip install oletools"
        )

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security.scanner import MalwareScanner

    # Workers need their own arq pool to enqueue follow-up jobs (e.g. check_bazaar).
    try:
        await init_arq_pool()
    except Exception as exc:
        logger.warning("ARQ pool init failed in worker (check_bazaar will be skipped): %s", exc)

    # Workers produce SSE events (e.g. auto-merge -> pr_approved) but never hold
    # client connections, so publish-only: no subscriber loop needed.
    from app.core.events.sse import start_sse_pubsub

    try:
        await start_sse_pubsub(subscribe=False)
    except Exception as exc:
        logger.warning("SSE pub/sub init failed in worker (live updates degraded): %s", exc)

    scanner = MalwareScanner()
    scanner.initialize()
    ctx["scanner"] = scanner

    from app.core.observability.telemetry import setup_telemetry

    setup_telemetry()

    # Provide a DB session factory for workers that need to persist upload state.
    _is_sqlite = settings.database_url.startswith("sqlite")
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        **({} if _is_sqlite else {"pool_size": 5, "max_overflow": 2}),
    )
    ctx["db_engine"] = engine
    ctx["db_sessionmaker"] = async_sessionmaker(engine, expire_on_commit=False)

    # Crash recovery is best-effort at startup and also runs periodically below.
    # Fresh intents are never reaped; the recovery helper enforces the persisted
    # remote-I/O deadline and stability probe under the global CAS lock.
    try:
        await recover_cas_storage_mutations(ctx)
    except Exception as exc:
        logger.warning("CAS mutation recovery at worker startup failed: %s", exc)

    print("Worker startup complete", flush=True)


async def shutdown(ctx: dict[str, Any]) -> None:
    from app.core.events.sse import stop_sse_pubsub
    from app.core.observability.telemetry import shutdown_telemetry

    await stop_sse_pubsub()
    await close_arq_pool()

    scanner = ctx.get("scanner")
    if scanner is not None:
        await scanner.close()

    engine = ctx.get("db_engine")
    if engine is not None:
        await engine.dispose()

    shutdown_telemetry()


# ── Queue name constants ──────────────────────────────────────────────────────

UPLOAD_FAST_QUEUE = "upload-fast"  # < 5 MiB files — dedicated fast workers
UPLOAD_SLOW_QUEUE = "upload-slow"  # ≥ 5 MiB files — dedicated slow workers


class WorkerSettings:
    """Main worker: handles all non-upload background tasks + fallback upload queue."""

    redis_settings = build_redis_settings()
    functions = [
        index_material,
        index_materials_batch,
        index_directory,
        index_directories_batch,
        delete_indexed_item,
        delete_storage_objects,
        add_cas_references,
        release_cas_references,
        release_storage_reservations,
        release_upload_quota,
        process_upload,
        process_upload_post_scan,
        dispatch_webhook,
        reset_14d_views,
        check_bazaar,
        dispatch_outbox,
    ]
    cron_jobs = [
        cron(cleanup_uploads, hour=3, minute=0),
        cron(gdpr_cleanup, hour=4, minute=0),
        cron(year_rollover, month={9}, day=1, hour=2, minute=0),
        cron(reconcile_multipart_uploads, hour={2, 14}, minute=0),
        cron(recover_cas_storage_mutations, minute=set(range(0, 60, 5))),
        cron(reset_daily_views, hour=0, minute=0),
        cron(reset_14d_views, day={1, 15}, hour=1, minute=0),
        cron(dispatch_outbox, minute=set(range(60))),
        cron(reconcile_search_documents, hour=5, minute=30),
    ]
    on_startup = startup
    on_shutdown = shutdown


class UploadFastWorkerSettings:
    """Dedicated worker for small uploads (< 5 MiB). Deploy separately for priority isolation."""

    redis_settings = build_redis_settings()
    queue_name = UPLOAD_FAST_QUEUE
    max_jobs = settings.worker_fast_max_jobs
    functions = [process_upload, process_upload_post_scan, check_bazaar]
    on_startup = startup
    on_shutdown = shutdown


class UploadSlowWorkerSettings:
    """Dedicated worker for large uploads (≥ 5 MiB). Deploy separately to avoid starving fast queue."""

    redis_settings = build_redis_settings()
    queue_name = UPLOAD_SLOW_QUEUE
    max_jobs = settings.worker_slow_max_jobs
    functions = [process_upload, process_upload_post_scan, check_bazaar]
    on_startup = startup
    on_shutdown = shutdown
