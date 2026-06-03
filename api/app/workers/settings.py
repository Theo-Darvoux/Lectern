from typing import Any

from arq.cron import cron

from app.config import settings
from app.core.redis import build_redis_settings, close_arq_pool, init_arq_pool
from app.workers.check_bazaar import check_bazaar
from app.workers.cleanup_uploads import cleanup_uploads
from app.workers.gdpr_cleanup import gdpr_cleanup
from app.workers.index_content import (
    delete_indexed_item,
    index_directories_batch,
    index_directory,
    index_material,
    index_materials_batch,
)
from app.workers.process_upload import process_upload
from app.workers.process_upload_post_scan import process_upload_post_scan
from app.workers.reconcile_multipart import reconcile_multipart_uploads
from app.workers.storage_ops import delete_storage_objects
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

    from app.core.scanner import MalwareScanner

    # Workers need their own arq pool to enqueue follow-up jobs (e.g. check_bazaar).
    try:
        await init_arq_pool()
    except Exception as exc:
        logger.warning("ARQ pool init failed in worker (check_bazaar will be skipped): %s", exc)

    scanner = MalwareScanner()
    scanner.initialize()
    ctx["scanner"] = scanner

    # Provide a DB session factory for workers that need to persist upload state.
    _is_sqlite = settings.database_url.startswith("sqlite")
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        **({} if _is_sqlite else {"pool_size": 5, "max_overflow": 2}),
    )
    ctx["db_engine"] = engine
    ctx["db_sessionmaker"] = async_sessionmaker(engine, expire_on_commit=False)


async def shutdown(ctx: dict[str, Any]) -> None:
    await close_arq_pool()

    scanner = ctx.get("scanner")
    if scanner is not None:
        await scanner.close()

    engine = ctx.get("db_engine")
    if engine is not None:
        await engine.dispose()


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
        process_upload,
        process_upload_post_scan,
        dispatch_webhook,
        reset_14d_views,
        check_bazaar,
    ]
    cron_jobs = [
        cron(cleanup_uploads, hour=3, minute=0),
        cron(gdpr_cleanup, hour=4, minute=0),
        cron(year_rollover, month={9}, day=1, hour=2, minute=0),
        cron(reconcile_multipart_uploads, hour={2, 14}, minute=0),
        cron(reset_daily_views, hour=0, minute=0),
        cron(reset_14d_views, day={1, 15}, hour=1, minute=0),
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
