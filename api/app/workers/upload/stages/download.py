import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.core.events.processing import ProcessingFile
from app.core.media.mimetypes import ZIP_MIME_TYPES, MimeRegistry, guess_mime_from_file_path
from app.core.observability.metrics import mime_category as _mime_cat
from app.core.security.async_utils import shielded_to_thread
from app.core.security.cas import hmac_cas_key
from app.core.security.file_security import check_svg_safety, get_uncompressed_size
from app.core.security.polyglot import check_polyglot
from app.core.storage.facade import download_file_with_hash, get_object_info
from app.schemas.material import UploadStatus
from app.workers.upload.constants import ensure_disk_space
from app.workers.upload.exceptions import MalwareError, UploadError

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    pf: ProcessingFile
    original_sha256: str
    initial_size: int
    actual_mime: str
    mime_category: str
    cas_key: str


async def run_download_and_validate(
    tmp_path: Path,
    quarantine_key: str,
    original_filename: str,
    mime_type: str,
    expected_sha256: str | None,
    upload_id: str,
) -> DownloadResult:
    info = await get_object_info(quarantine_key)
    initial_size = info["size"]
    download_limit = settings.max_file_size_mb * 1024 * 1024
    if initial_size > download_limit:
        raise UploadError(UploadStatus.FAILED, "File exceeds configured upload size limit")

    # Cut-off for suspicious expansion, matching ZIP_MAX_TOTAL_BYTES.
    expansion_hard_limit = 500 * 1024 * 1024
    required_free = int(initial_size * 2.0)

    ensure_disk_space(tmp_path, required_free)

    original_sha256 = await download_file_with_hash(
        quarantine_key,
        tmp_path,
        max_bytes=download_limit,
        expected_size=initial_size,
    )

    is_zip_family = mime_type in ZIP_MIME_TYPES or original_filename.lower().endswith(
        (".docx", ".xlsx", ".pptx", ".zip", ".epub")
    )
    if is_zip_family:
        uncompressed_size = await shielded_to_thread(get_uncompressed_size, tmp_path)
        if uncompressed_size > expansion_hard_limit:
            msg = (
                "Decompression bomb detected: total uncompressed size "
                f"{uncompressed_size} bytes exceeds limit."
            )
            raise MalwareError(msg)

        required_extraction_free = int(uncompressed_size * 1.2)
        ensure_disk_space(tmp_path, required_extraction_free)

    if expected_sha256 and expected_sha256 != original_sha256:
        msg = "SHA-256 integrity check failed"
        logger.warning(
            "Upload %s failed sha256 check. Expected: %s, got: %s",
            upload_id,
            expected_sha256,
            original_sha256,
        )
        raise UploadError(UploadStatus.FAILED, msg)

    detected_mime = guess_mime_from_file_path(tmp_path)
    actual_mime = MimeRegistry.get_authoritative_mime(original_filename, detected_mime)
    declared_mime = MimeRegistry.normalize_mime(mime_type)
    if actual_mime != "application/octet-stream" and actual_mime != declared_mime:
        logger.info(
            "MIME mismatch for %s: declared %s, detected %s",
            upload_id,
            declared_mime,
            actual_mime,
        )
    else:
        actual_mime = declared_mime

    mime_category = _mime_cat(actual_mime)

    if not MimeRegistry.is_allowed_mime(actual_mime):
        msg = f"File type {actual_mime} is not allowed"
        raise UploadError(UploadStatus.FAILED, msg)

    try:
        await shielded_to_thread(check_polyglot, tmp_path, actual_mime)
    except ValueError as exc:
        raise MalwareError(str(exc))

    if actual_mime == "image/svg+xml":
        try:
            check_svg_safety(tmp_path.read_bytes(), original_filename)
        except Exception as exc:
            raise MalwareError(str(exc))

    pf = ProcessingFile(tmp_path, size=initial_size)
    cas_key = hmac_cas_key(original_sha256)

    return DownloadResult(
        pf=pf,
        original_sha256=original_sha256,
        initial_size=initial_size,
        actual_mime=actual_mime,
        mime_category=mime_category,
        cas_key=cas_key,
    )
