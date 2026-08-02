"""Background ARQ worker: fail-open MalwareBazaar post-promotion check.

This worker is enqueued fire-and-forget by the upload pipeline immediately after
a file is promoted to ``cas/`` storage with status=CLEAN.  It performs the
MalwareBazaar hash lookup that was skipped in the hot path, then calls
``retroactive_quarantine`` if the file is flagged.

Design invariants
-----------------
* Idempotent: a Redis tombstone (``bazaar:clean:{sha256}``) prevents duplicate
  Bazaar calls for the same hash.  Re-uploads of the same file skip this worker
  entirely.
* This worker is used by fail-open mode. It still re-raises if configuration is
  changed to fail-closed while an already-queued job is running.
* No hard dependency on the scanner pool: falls back to a one-shot scanner when
  the context scanner is unavailable (e.g. in integration tests).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.core.common.exceptions import ServiceUnavailableError
from app.workers.retroactive_quarantine import retroactive_quarantine
from app.workers.upload.context import WorkerContext
from app.workers.upload.pipeline import _get_fallback_scanner

logger = logging.getLogger(__name__)

# Redis key prefixes used by this worker.
_BAZAAR_CLEAN_PREFIX = "bazaar:clean:"
_BAZAAR_SKIPPED_PREFIX = "bazaar:skipped:"

# TTL for "known-clean" tombstones (7 days).  Balances false-negative risk (new
# Bazaar entries for the same hash) against redundant network calls.
_CLEAN_TOMBSTONE_TTL = 7 * 24 * 3600  # seconds
# TTL for "skipped due to error" tombstones (1 hour).  Short so the worker retries
# on the next upload of the same file if Bazaar recovers.
_SKIPPED_TOMBSTONE_TTL = 3600  # seconds


async def check_bazaar(
    ctx: dict[str, Any],
    *,
    upload_id: str,
    sha256: str,
    cas_s3_key: str,
    user_id: str,
) -> None:
    """Query MalwareBazaar for a promoted file and quarantine retroactively if flagged.

    Args:
        ctx:        ARQ worker context dict.
        upload_id:  The internal upload identifier (for DB lookup and SSE events).
        sha256:     SHA-256 of the *original* (pre-compression) file content.
        cas_s3_key: The ``cas/<id>`` S3 key the file was promoted to.
        user_id:    Uploader's user UUID string (for quota cleanup).
    """
    wctx = WorkerContext.from_arq_ctx(ctx)
    redis = wctx.redis

    # ── Idempotency check ────────────────────────────────────────────────────
    # If we already verified this hash as clean, skip the network call entirely.
    # This covers re-uploads of popular documents (e.g. course PDFs uploaded by
    # many students).
    tombstone = await redis.get(f"{_BAZAAR_CLEAN_PREFIX}{sha256}")
    if tombstone:
        logger.debug("check_bazaar: sha256=%s already tombstoned clean, skipping", sha256)
        return

    # ── MalwareBazaar lookup ─────────────────────────────────────────────────
    scanner = wctx.scanner
    owns_scanner = scanner is None
    if scanner is None:
        scanner = _get_fallback_scanner()

    try:
        threat = await scanner.check_malwarebazaar(sha256, upload_id)
    except (httpx.TimeoutException, httpx.HTTPError, ServiceUnavailableError) as exc:
        if owns_scanner:
            await scanner.close()
        if settings.malwarebazaar_fail_closed:
            logger.error(
                "check_bazaar: Bazaar call failed for upload %s (fail-closed) — "
                "re-raising for ARQ retry: %s",
                upload_id,
                exc,
            )
            raise  # ARQ will retry up to _MAX_ARQ_RETRIES
        # Fail-open: YARA already cleared this file; log and move on.
        logger.warning(
            "check_bazaar: Bazaar call failed for upload %s (fail-open): %s — "
            "writing skip tombstone and continuing.",
            upload_id,
            exc,
        )
        await redis.set(f"{_BAZAAR_SKIPPED_PREFIX}{sha256}", "1", ex=_SKIPPED_TOMBSTONE_TTL)
        return
    except Exception:
        if owns_scanner:
            await scanner.close()
        raise
    else:
        if owns_scanner:
            await scanner.close()

    # ── Decision ─────────────────────────────────────────────────────────────
    if threat is None:
        # File is clean.  Write tombstone so future uploads of the same file
        # skip this worker entirely.
        await redis.set(f"{_BAZAAR_CLEAN_PREFIX}{sha256}", "1", ex=_CLEAN_TOMBSTONE_TTL)
        logger.info(
            "check_bazaar: upload %s (sha256=%.16s…) is clean per MalwareBazaar.",
            upload_id,
            sha256,
        )
        return

    # File is flagged.  Run retroactive quarantine.
    logger.warning(
        "check_bazaar: MalwareBazaar flagged upload %s (sha256=%.16s…) as '%s'. "
        "Initiating retroactive quarantine.",
        upload_id,
        sha256,
        threat,
    )
    await retroactive_quarantine(
        wctx,
        upload_id=upload_id,
        sha256=sha256,
        cas_s3_key=cas_s3_key,
        user_id=user_id,
        threat=threat,
    )
