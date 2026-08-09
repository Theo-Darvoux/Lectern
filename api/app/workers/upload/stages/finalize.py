import contextlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.events.processing import ProcessingFile
from app.core.media.mimetypes import MimeRegistry
from app.core.security.cas import hmac_cas_key, increment_cas_ref
from app.core.storage.capacity import release_storage_reservation
from app.core.storage.facade import upload_file_multipart
from app.workers.upload.constants import _SCAN_CACHE_PREFIX

logger = logging.getLogger(__name__)


@dataclass
class FinalizeInput:
    pf: ProcessingFile
    user_id: str
    upload_id: str
    original_filename: str
    original_sha256: str
    cas_key: str
    initial_size: int
    final_mime: str
    content_encoding: str | None = None
    thumbnail_path: str | None = None


@dataclass
class FinalizeResult:
    final_key: str
    final_size: int
    content_sha256: str
    new_cas_ref: int
    db_cas_key: str
    safe_name: str
    thumbnail_key: str | None = None


async def run_finalize_storage(
    input_data: FinalizeInput,
    redis_client: Any,
    tracer: Any,
) -> FinalizeResult:
    with tracer.start_as_current_span("upload.finalize") as final_span:
        ext = MimeRegistry.get_canonical_extension(input_data.final_mime)
        safe_name = input_data.original_filename
        if ext and not input_data.original_filename.lower().endswith(ext.lower()):
            stem = Path(input_data.original_filename).stem
            safe_name = f"{stem}{ext}"

        content_sha256 = await input_data.pf.sha256()
        # CAS identity is the exact sanitized byte sequence that is stored.
        content_cas_key = hmac_cas_key(content_sha256)
        cas_id = content_cas_key.split(":")[-1]
        cas_s3_key = f"cas/{cas_id}"

        final_span.set_attribute("upload.final_key", cas_s3_key)

        # Concurrent writes for the same content hash contain identical bytes.
        await upload_file_multipart(
            input_data.pf.path,
            cas_s3_key,
            content_type=input_data.final_mime,
            content_encoding=input_data.content_encoding,
        )

        thumbnail_key = None
        if input_data.thumbnail_path:
            thumbnail_key = f"thumbnails/{cas_id}/{input_data.upload_id}.webp"
            await upload_file_multipart(
                Path(input_data.thumbnail_path),
                thumbnail_key,
                content_type="image/webp",
            )
            # Cleanup thumbnail temp file
            with contextlib.suppress(Exception):
                Path(input_data.thumbnail_path).unlink(missing_ok=True)

    final_size = input_data.pf.size

    await redis_client.set(f"{_SCAN_CACHE_PREFIX}{cas_s3_key}", "CLEAN", ex=24 * 3600)

    cas_data = {
        "final_key": cas_s3_key,
        "mime_type": input_data.final_mime,
        "size": final_size,
        "file_name": safe_name,
        "scanned_at": time.time(),
    }
    new_cas_ref = await increment_cas_ref(
        redis_client,
        content_sha256,
        initial_data=cas_data,
        operation_id=f"upload-finalize:{input_data.upload_id}",
    )
    # The in-flight object is now represented by authoritative CAS usage.
    try:
        await release_storage_reservation(input_data.upload_id, redis_client)
    except Exception as exc:
        # Reservations expire and are reconciled by the next reservation attempt.
        logger.warning(
            "Failed to release storage reservation for upload %s: %s",
            input_data.upload_id,
            exc,
        )
    db_cas_key = content_cas_key

    # Quota tracking: use a synthetic staging key (no S3 object) so quota
    # cleanup doesn't interfere with shared CAS objects.
    staging_quota_key = f"staging:{input_data.user_id}:{input_data.upload_id}"
    await redis_client.zadd(f"quota:uploads:{input_data.user_id}", {staging_quota_key: time.time()})

    return FinalizeResult(
        final_key=cas_s3_key,
        final_size=final_size,
        content_sha256=content_sha256,
        new_cas_ref=new_cas_ref,
        db_cas_key=db_cas_key,
        safe_name=safe_name,
        thumbnail_key=thumbnail_key,
    )
