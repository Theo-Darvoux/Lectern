import logging
from typing import Any

from app.core.observability.telemetry import extract_trace_context
from app.workers.upload.constants import _STAGES, _overall
from app.workers.upload.context import WorkerContext
from app.workers.upload.pipeline import UploadPipeline, _get_fallback_scanner

logger = logging.getLogger(__name__)


async def process_upload(
    ctx: dict[str, Any],
    user_id: str,
    upload_id: str,
    quarantine_key: str,
    original_filename: str,
    mime_type: str,
    expected_sha256: str | None = None,
    trace_context: dict[str, Any] | None = None,
) -> None:
    """Background task: download -> scan -> strip metadata -> immutable CAS finalize.

    The parallel scan+strip path uses asyncio.gather in
    app.workers.upload.stages.scan_strip.run_scan_and_strip with _run_scan and _run_strip.
    The CAS key is still derived from hmac_cas_key(original_sha256).
    """
    from opentelemetry import context as otel_context

    worker_ctx = WorkerContext.from_arq_ctx(ctx)
    trace_ctx = extract_trace_context(trace_context or {})
    otel_token = otel_context.attach(trace_ctx)
    try:
        pipeline = UploadPipeline(
            worker_ctx,
            user_id=user_id,
            upload_id=upload_id,
            quarantine_key=quarantine_key,
            original_filename=original_filename,
            mime_type=mime_type,
            expected_sha256=expected_sha256,
        )
        await pipeline.run()
    finally:
        otel_context.detach(otel_token)


# Export these for tests
__all__ = [
    "process_upload",
    "_STAGES",
    "_overall",
    "_get_fallback_scanner",
]
