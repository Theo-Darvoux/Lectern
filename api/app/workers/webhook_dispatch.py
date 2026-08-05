"""Webhook dispatch ARQ job.

Reads an Upload row, signs a JSON payload with HMAC-SHA256, and POSTs it to the
registered webhook_url.  Retries with exponential back-off up to 3 times.

Payload shape
-------------
{
  "event":      "upload.complete",
  "upload_id":  "<uuid>",
  "status":     "clean",
  "file_key":   "<s3-key or null>",
  "mime_type":  "<mime or null>",
  "size":       <bytes or null>,
  "sha256":     "<hex or null>",
  "timestamp":  "<ISO-8601 UTC>"
}

Signature
---------
Each request includes two headers:
  X-Lectern-Signature: sha256=<hex>
  X-Lectern-Delivery:  <random UUID per attempt>

The HMAC is computed over the UTF-8-encoded JSON body using the webhook secret
(``settings.webhook_secret``, which falls back to ``settings.secret_key``).
"""

import contextlib
import hashlib
import hmac
import inspect
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from app.config import settings
from app.core.database.redis import redis_lock
from app.core.observability.metrics import upload_webhook_total
from app.core.security.url_validation import (
    PinnedRequestError,
    post_pinned_https,
    resolve_safe_url,
    resolve_safe_url_async,
)
from app.routers.upload.cancellation import upload_lifecycle_lock_name

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (30, 120, 480)  # 30s, 2m, 8m — ARQ-deferred between attempts
_TIMEOUT_SECONDS = 10.0


def _signing_secret() -> bytes:
    """Return the HMAC secret as bytes."""
    secret = settings.webhook_secret or settings.secret_key.get_secret_value()
    return secret.encode()


def _sign(body: bytes) -> str:
    """Compute HMAC-SHA256 signature over ``body``. Returns ``sha256=<hex>``."""
    sig = hmac.new(_signing_secret(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def validate_webhook_url(url: str) -> bool:
    """Validate a webhook URL to prevent SSRF. Delegates to core/url_validation.py."""
    return resolve_safe_url(url) is not None


async def _deliver_webhook_once(
    session_factory: Any,
    *,
    upload_id: str,
    attempt: int,
) -> str | None:
    """Deliver while holding the authoritative upload row lock.

    Returning a string requests a deferred retry. ``None`` means the attempt was
    delivered or authoritatively skipped. Database failures propagate so ARQ can
    retry instead of silently losing the completion event.
    """
    from sqlalchemy import select

    from app.models.upload import Upload

    async with session_factory() as session:
        row = await session.scalar(
            select(Upload).where(Upload.upload_id == upload_id).with_for_update()
        )
        if row is None:
            logger.warning("dispatch_webhook: Upload %s not found in DB — skipping", upload_id)
            upload_webhook_total.labels(outcome="skipped").inc()
            return None
        if not row.webhook_url:
            upload_webhook_total.labels(outcome="skipped").inc()
            return None
        if row.status not in ("clean", "applied") or not row.final_key:
            logger.info(
                "dispatch_webhook: upload %s is no longer publishable (status=%s) — skipping",
                upload_id,
                row.status,
            )
            upload_webhook_total.labels(outcome="skipped").inc()
            return None
        if (
            row.status == "clean"
            and row.final_key.startswith("cas/")
            and int(row.cas_ref_count or 0) <= 0
        ):
            logger.info(
                "dispatch_webhook: upload %s no longer owns its CAS object — skipping",
                upload_id,
            )
            upload_webhook_total.labels(outcome="skipped").inc()
            return None

        resolved_target = await resolve_safe_url_async(row.webhook_url)
        if resolved_target is None:
            logger.warning("dispatch_webhook: invalid webhook URL %s — skipping", row.webhook_url)
            upload_webhook_total.labels(outcome="skipped").inc()
            return None

        payload = {
            "event": "upload.complete",
            "upload_id": row.upload_id,
            # Applied uploads transferred ownership into MaterialVersion, but
            # the immutable event being delivered is still upload completion.
            "status": "clean",
            "file_key": row.final_key,
            "mime_type": getattr(row, "mime_type", None),
            "size": getattr(row, "size_bytes", None),
            "sha256": row.sha256,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Lectern-Signature": _sign(body),
            "X-Lectern-Delivery": str(uuid.uuid4()),
            "X-Lectern-Event": "upload.complete",
            "User-Agent": "Lectern-Webhook/1.0",
        }

        try:
            response = await post_pinned_https(
                resolved_target,
                content=body,
                headers=headers,
                timeout=_TIMEOUT_SECONDS,
            )
        except PinnedRequestError as exc:
            return str(exc)

        if response.is_success:
            logger.info(
                "Webhook delivered for upload %s (attempt %d/%d, status %d)",
                upload_id,
                attempt,
                _MAX_ATTEMPTS,
                response.status_code,
            )
            upload_webhook_total.labels(outcome="success").inc()
            return None
        if response.status_code < 500:
            logger.warning(
                "Webhook HTTP %d for upload %s (permanent, not retrying)",
                response.status_code,
                upload_id,
            )
            upload_webhook_total.labels(outcome="http_error").inc()
            return None
        return f"HTTP {response.status_code}"


async def dispatch_webhook(ctx: dict, *, upload_id: str, attempt: int = 1) -> None:  # type: ignore[type-arg]
    """Deliver one immutable completion event serialized with terminal transitions."""
    session_factory = ctx.get("db_sessionmaker")
    if session_factory is None:
        logger.warning(
            "dispatch_webhook: no db_sessionmaker in ctx — skipping upload %s", upload_id
        )
        upload_webhook_total.labels(outcome="skipped").inc()
        return

    coordination_redis = ctx.get("redis")
    supports_lifecycle_lock = coordination_redis is not None and not inspect.iscoroutinefunction(
        getattr(coordination_redis, "register_script", None)
    )
    lifecycle_guard = (
        redis_lock(
            cast(Any, coordination_redis),
            upload_lifecycle_lock_name(upload_id),
            timeout=120.0,
            expire=300.0,
        )
        if supports_lifecycle_lock
        else contextlib.nullcontext()
    )

    try:
        async with lifecycle_guard:
            transient_failure = await _deliver_webhook_once(
                session_factory,
                upload_id=upload_id,
                attempt=attempt,
            )
    except Exception as exc:
        logger.error("dispatch_webhook: delivery attempt failed for upload %s: %s", upload_id, exc)
        raise

    if transient_failure is None:
        return

    # Retry scheduling happens after releasing all lifecycle/row locks. A
    # terminal transition may win before the retry, which then exits above.
    logger.warning(
        "Webhook transient failure for upload %s (attempt %d/%d): %s",
        upload_id,
        attempt,
        _MAX_ATTEMPTS,
        transient_failure,
    )
    if attempt < _MAX_ATTEMPTS:
        from datetime import timedelta

        backoff = _BACKOFF_SECONDS[attempt - 1]
        arq = ctx.get("redis") or ctx.get("arq")
        if arq is not None:
            await arq.enqueue_job(
                "dispatch_webhook",
                upload_id=upload_id,
                attempt=attempt + 1,
                _defer_by=timedelta(seconds=backoff),
            )
            logger.info(
                "Webhook re-enqueued for upload %s (attempt %d → %d, defer %ds)",
                upload_id,
                attempt,
                attempt + 1,
                backoff,
            )
            return

    logger.error(
        "Webhook delivery failed for upload %s after %d attempts", upload_id, _MAX_ATTEMPTS
    )
    upload_webhook_total.labels(outcome="network_error").inc()

    try:
        from app.workers.upload.context import WorkerContext
        from app.workers.upload.repository import UploadWorkerRepository

        repo = UploadWorkerRepository(WorkerContext.from_arq_ctx(ctx))
        await repo.insert_dead_letter(
            upload_id=upload_id,
            job_name="dispatch_webhook",
            payload={"upload_id": upload_id, "attempt": attempt},
            error=transient_failure,
            attempts=attempt,
        )
    except Exception as dlq_exc:
        logger.error("Failed to insert webhook dead letter for upload %s: %s", upload_id, dlq_exc)
