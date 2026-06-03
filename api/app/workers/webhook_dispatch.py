"""Webhook dispatch ARQ job.

Reads an Upload row, signs a JSON payload with HMAC-SHA256, and POSTs it to the
registered webhook_url.  Retries with exponential back-off up to 3 times.

Payload shape
-------------
{
  "event":      "upload.complete",
  "upload_id":  "<uuid>",
  "status":     "clean" | "malicious" | "failed",
  "file_key":   "<s3-key or null>",
  "mime_type":  "<mime or null>",
  "size":       <bytes or null>,
  "sha256":     "<hex or null>",
  "timestamp":  "<ISO-8601 UTC>"
}

Signature
---------
Each request includes two headers:
  X-WikINT-Signature: sha256=<hex>
  X-WikINT-Delivery:  <random UUID per attempt>

The HMAC is computed over the UTF-8-encoded JSON body using the webhook secret
(``settings.webhook_secret``, which falls back to ``settings.secret_key``).
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx

from app.config import settings
from app.core.metrics import upload_webhook_total
from app.core.url_validation import is_safe_url

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
    return is_safe_url(url)


async def dispatch_webhook(ctx: dict, *, upload_id: str, attempt: int = 1) -> None:  # type: ignore[type-arg]
    """ARQ job: look up the Upload row and POST the signed payload to the webhook URL.

    On transient failure (network error or 5xx), re-enqueues itself via ARQ with
    exponential backoff (30s, 2m, 8m).  After _MAX_ATTEMPTS failures, inserts a
    dead-letter record and gives up.  Permanent errors (4xx, bad URL) are not retried.
    """
    session_factory = ctx.get("db_sessionmaker")
    if session_factory is None:
        logger.warning(
            "dispatch_webhook: no db_sessionmaker in ctx — skipping upload %s", upload_id
        )
        upload_webhook_total.labels(outcome="skipped").inc()
        return

    # ── Load Upload row ───────────────────────────────────────────────────────
    try:
        from sqlalchemy import select

        from app.models.upload import Upload

        async with session_factory() as session:
            row = await session.scalar(select(Upload).where(Upload.upload_id == upload_id))
    except Exception as exc:
        logger.error("dispatch_webhook: DB read failed for upload %s: %s", upload_id, exc)
        upload_webhook_total.labels(outcome="skipped").inc()
        return

    if row is None:
        logger.warning("dispatch_webhook: Upload %s not found in DB — skipping", upload_id)
        upload_webhook_total.labels(outcome="skipped").inc()
        return

    if not row.webhook_url:
        upload_webhook_total.labels(outcome="skipped").inc()
        return

    # ── SSRF Validation ───────────────────────────────────────────────────────
    if not validate_webhook_url(row.webhook_url):
        logger.warning("dispatch_webhook: invalid webhook URL %s — skipping", row.webhook_url)
        upload_webhook_total.labels(outcome="skipped").inc()
        return

    # ── Build payload ─────────────────────────────────────────────────────────
    payload = {
        "event": "upload.complete",
        "upload_id": row.upload_id,
        "status": row.status,
        "file_key": row.final_key,
        "mime_type": getattr(row, "mime_type", None),
        "size": getattr(row, "size_bytes", None),
        "sha256": row.sha256,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = _sign(body)

    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-WikINT-Signature": signature,
        "X-WikINT-Delivery": delivery_id,
        "X-WikINT-Event": "upload.complete",
        "User-Agent": "WikINT-Webhook/1.0",
    }

    # ── Single delivery attempt ───────────────────────────────────────────────
    transient_failure: str | None = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(row.webhook_url, content=body, headers=headers)
        if response.is_success:
            logger.info(
                "Webhook delivered for upload %s (attempt %d/%d, status %d)",
                upload_id,
                attempt,
                _MAX_ATTEMPTS,
                response.status_code,
            )
            upload_webhook_total.labels(outcome="success").inc()
            return
        elif response.status_code < 500:
            # 4xx: permanent client error — don't retry
            logger.warning(
                "Webhook HTTP %d for upload %s (permanent, not retrying)",
                response.status_code,
                upload_id,
            )
            upload_webhook_total.labels(outcome="http_error").inc()
            return
        else:
            transient_failure = f"HTTP {response.status_code}"
    except httpx.TransportError as exc:
        transient_failure = str(exc)

    logger.warning(
        "Webhook transient failure for upload %s (attempt %d/%d): %s",
        upload_id,
        attempt,
        _MAX_ATTEMPTS,
        transient_failure,
    )

    # ── Exponential backoff via ARQ re-enqueue ────────────────────────────────
    if attempt < _MAX_ATTEMPTS:
        from datetime import timedelta

        backoff = _BACKOFF_SECONDS[attempt - 1]
        arq = ctx.get("arq")
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

    # ── All attempts exhausted — insert dead-letter record ────────────────────
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
            error=transient_failure or "unknown",
            attempts=attempt,
        )
    except Exception as dlq_exc:
        logger.error("Failed to insert webhook dead letter for upload %s: %s", upload_id, dlq_exc)
