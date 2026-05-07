"""POST /api/upload/batch-zip -- upload a zip of files, extract and process individually.

Security model:
- Zip bomb: declared uncompressed size limit + extraction byte counter
- Zip slip: all entry paths are validated for traversal sequences before extraction
- Symlinks: skipped entirely (Unix external_attr check)
- OS metadata: __MACOSX/, .DS_Store, ._* skipped automatically
- Per-file validation: same extension whitelist and MIME detection as direct upload
- Per-file quota: each file counts against the user's pending upload cap
- Max members: configurable hard cap (200 regular / 2 000 privileged)
"""

import asyncio
import logging
import mimetypes
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, UploadFile
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MAGIC_HEADER_SIZE, PRIVILEGED_ROLES
from app.core.database import get_db
from app.core.exceptions import BadRequestError
from app.core.file_security import SvgSecurityError
from app.core.mimetypes import guess_mime_from_bytes
from app.core.processing import ProcessingFile
from app.core.redis import get_redis
from app.core.storage import get_s3_client
from app.core.upload_errors import (
    ERR_BATCH_TOO_LARGE,
    ERR_INVALID_ZIP,
    ERR_ZIP_BOMB,
)
from app.dependencies.auth import CurrentUser
from app.dependencies.rate_limit import rate_limit_uploads
from app.routers.upload.helpers import (
    _check_pending_cap,
    _check_storage_limit,
    _create_upload_row,
    _enqueue_processing,
)
from app.routers.upload.validators import (
    _apply_mime_correction,
    _check_per_type_size,
    _validate_filename,
)
from app.schemas.material import BatchZipEntry, BatchZipResponse
from app.services.auth import get_full_auth_config

logger = logging.getLogger("wikint")

router = APIRouter()

# ── Security limits ───────────────────────────────────────────────────────────

_MAX_ZIP_BYTES = 500 * 1024 * 1024  # 500 MiB — the zip file itself
_MAX_MEMBERS = 200  # regular users
_MAX_MEMBERS_PRIVILEGED = 2_000  # moderator / bureau / vieux
_MAX_TOTAL_EXTRACTED_BYTES = 2 * 1024**3  # 2 GiB total uncompressed
_MAX_COMPRESSION_RATIO = 100  # uncompressed/compressed ratio (zip bomb)
_MAX_PATH_DEPTH = 20  # folder nesting depth within zip

# OS-generated junk to skip silently
_SKIP_PREFIXES = ("__MACOSX/",)
_SKIP_BASENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep"})
_SKIP_BASENAME_PREFIXES = ("._",)

# Concurrent S3 uploads for extracted files
_UPLOAD_CONCURRENCY = 4


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_safe_zip_path(path: str) -> bool:
    """Return True if the zip entry path is free of traversal sequences."""
    norm = path.replace("\\", "/")
    if norm.startswith("/"):
        return False
    if "\x00" in norm:
        return False
    return all(part != ".." for part in norm.split("/"))


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """Return True if the entry represents a Unix symlink (external_attr check)."""
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _should_skip_metadata(info: zipfile.ZipInfo) -> bool:
    """Return True for directories, symlinks, and OS-generated junk files."""
    fname = info.filename
    if fname.endswith("/") or info.file_size == 0 and fname.endswith("/"):
        return True
    if _is_symlink_entry(info):
        return True
    basename = os.path.basename(fname)
    if basename in _SKIP_BASENAMES:
        return True
    for pfx in _SKIP_BASENAME_PREFIXES:
        if basename.startswith(pfx):
            return True
    return any(fname.startswith(pfx) for pfx in _SKIP_PREFIXES)


@dataclass
class _ExtractedEntry:
    tmp_path: Path
    filename: str  # sanitized basename
    relative_path: str  # path within the zip (slash-separated)
    size: int


def _extract_zip_sync(
    zip_path: str,
    tmp_dir: str,
    max_members: int,
) -> tuple[list[_ExtractedEntry], list[str]]:
    """
    Extract zip to individual temp files.  Runs synchronously — call via to_thread.

    Returns (entries, skipped_paths).
    Raises BadRequestError on security violations (fail-fast for zip slip / zip bomb).
    """
    try:
        zf_obj = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile:
        raise BadRequestError("Not a valid zip archive.", code=ERR_INVALID_ZIP)

    with zf_obj as zf:
        all_members = zf.infolist()

        # Phase 1 — path safety scan (fail the entire zip on first violation)
        for info in all_members:
            if not _should_skip_metadata(info) and not _is_safe_zip_path(info.filename):
                raise BadRequestError(
                    f"Zip contains an unsafe path and was rejected: {info.filename!r}",
                    code=ERR_INVALID_ZIP,
                )

        file_members = [m for m in all_members if not _should_skip_metadata(m)]

        # Member count limit
        if len(file_members) > max_members:
            raise BadRequestError(
                f"Zip contains {len(file_members)} files; maximum allowed is {max_members}.",
                code=ERR_BATCH_TOO_LARGE,
            )

        # Total uncompressed size declared in headers
        total_declared = sum(m.file_size for m in file_members)
        if total_declared > _MAX_TOTAL_EXTRACTED_BYTES:
            raise BadRequestError(
                f"Zip would extract to {total_declared // (1024**3):.1f} GiB; "
                f"limit is {_MAX_TOTAL_EXTRACTED_BYTES // (1024**3):.0f} GiB.",
                code=ERR_ZIP_BOMB,
            )

        # Compression ratio check (zip bomb via header vs payload divergence)
        total_compressed = sum(m.compress_size for m in file_members)
        if total_compressed > 0:
            ratio = total_declared / total_compressed
            if ratio > _MAX_COMPRESSION_RATIO:
                raise BadRequestError(
                    f"Zip compression ratio ({ratio:.0f}x) exceeds safety limit.",
                    code=ERR_ZIP_BOMB,
                )

        # Path depth check
        for m in file_members:
            depth = len(m.filename.rstrip("/").split("/"))
            if depth > _MAX_PATH_DEPTH:
                raise BadRequestError(
                    f"Zip entry is nested too deeply ({depth} levels): {m.filename!r}",
                    code=ERR_INVALID_ZIP,
                )

        # Phase 2 — extraction with hard per-entry byte limit
        entries: list[_ExtractedEntry] = []
        skipped: list[str] = []
        total_bytes_read = 0
        chunk = 64 * 1024

        for idx, info in enumerate(file_members):
            tmp_path = Path(tmp_dir) / f"entry_{idx}"
            bytes_written = 0

            try:
                with zf.open(info) as src, open(tmp_path, "wb") as dst:
                    while True:
                        data = src.read(chunk)
                        if not data:
                            break
                        bytes_written += len(data)
                        # Hard extraction limit (catches decompression bombs)
                        if (
                            bytes_written > info.file_size + 1024
                            or total_bytes_read + bytes_written > _MAX_TOTAL_EXTRACTED_BYTES
                        ):
                            dst.close()
                            tmp_path.unlink(missing_ok=True)
                            raise BadRequestError(
                                "Zip entry decompresses larger than declared; possible zip bomb.",
                                code=ERR_ZIP_BOMB,
                            )
                        dst.write(data)
            except zipfile.BadZipFile:
                skipped.append(f"{info.filename}: corrupt entry, skipped")
                tmp_path.unlink(missing_ok=True)
                continue

            total_bytes_read += bytes_written
            sanitized = os.path.basename(info.filename)
            entries.append(
                _ExtractedEntry(
                    tmp_path=tmp_path,
                    filename=sanitized,
                    relative_path=info.filename.rstrip("/"),
                    size=bytes_written,
                )
            )

        return entries, skipped


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.post("/batch-zip", response_model=BatchZipResponse, status_code=202)
async def upload_batch_zip(
    file: UploadFile,
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    _: Annotated[None, Depends(rate_limit_uploads)],
) -> BatchZipResponse:
    """Upload a zip file; extract and queue each contained file individually.

    Returns a ``BatchZipResponse`` with one ``BatchZipEntry`` per successfully
    queued file.  Each entry includes a ``quarantine_key`` the client uses to
    subscribe to the processing SSE stream, exactly as with a direct upload.

    Files that fail per-type size limits, extension validation, or quota checks
    are skipped and reported in the ``errors`` list.  A zip that contains unsafe
    paths (zip slip) or triggers zip-bomb heuristics is rejected entirely (4xx).
    """
    user_id = str(user.id)
    privileged = user.role in PRIVILEGED_ROLES
    max_members = _MAX_MEMBERS_PRIVILEGED if privileged else _MAX_MEMBERS

    config = await get_full_auth_config(db, redis)

    allowed_exts: set[str] | None = None
    if config.get("allowed_extensions"):
        raw = config["allowed_extensions"]
        parts = raw.split(",") if isinstance(raw, str) else list(raw)
        allowed_exts = {
            (e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}")
            for e in parts
            if e.strip()
        }

    allowed_mimes: set[str] | None = None
    if config.get("allowed_mime_types"):
        raw = config["allowed_mime_types"]
        parts = raw.split(",") if isinstance(raw, str) else list(raw)
        allowed_mimes = {m.strip().lower() for m in parts if m.strip()}

    tmp_dir = tempfile.mkdtemp(prefix="wikint_bz_")
    zip_path = os.path.join(tmp_dir, "upload.zip")

    try:
        # ── Stream zip to disk ──────────────────────────────────────────────
        bytes_written = 0
        _read_chunk = 64 * 1024
        with open(zip_path, "wb") as fh:
            while True:
                chunk = await file.read(_read_chunk)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _MAX_ZIP_BYTES:
                    raise BadRequestError(
                        f"Zip file exceeds {_MAX_ZIP_BYTES // (1024**2)} MiB limit.",
                        code=ERR_BATCH_TOO_LARGE,
                    )
                fh.write(chunk)

        if bytes_written == 0:
            raise BadRequestError("Empty zip file.", code=ERR_INVALID_ZIP)

        # Quick magic-byte check before full parse
        with open(zip_path, "rb") as fh:  # type: ignore[assignment]
            magic = fh.read(4)
        if magic not in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
            raise BadRequestError("File is not a valid zip archive.", code=ERR_INVALID_ZIP)

        # ── Extract (runs in thread to avoid blocking event loop) ────────────
        entries, extract_skipped = await asyncio.to_thread(
            _extract_zip_sync, zip_path, tmp_dir, max_members
        )

        if not entries:
            return BatchZipResponse(
                files=[],
                skipped=len(extract_skipped),
                errors=extract_skipped or ["No valid files found in zip."],
            )

        # ── Process each extracted file through the upload pipeline ──────────
        results: list[BatchZipEntry] = []
        per_file_errors: list[str] = list(extract_skipped)
        skipped_count = len(extract_skipped)

        semaphore = asyncio.Semaphore(_UPLOAD_CONCURRENCY)

        async def _process_one(entry: _ExtractedEntry) -> BatchZipEntry | None:
            nonlocal skipped_count
            async with semaphore:
                upload_id = str(uuid4())
                try:
                    # Validate filename & extension
                    try:
                        safe_name, ext = _validate_filename(
                            entry.filename, allowed_extensions=allowed_exts
                        )
                    except BadRequestError as exc:
                        per_file_errors.append(f"{entry.filename}: {exc.detail}")
                        skipped_count += 1
                        return None

                    # MIME detection
                    pf = ProcessingFile(entry.tmp_path, entry.size)
                    with pf.open("rb") as fh:
                        head = fh.read(MAGIC_HEADER_SIZE)

                    real_mime = guess_mime_from_bytes(head)
                    if real_mime != "application/octet-stream":
                        try:
                            safe_name, ext = _apply_mime_correction(
                                safe_name, real_mime, ext, allowed_mimes=allowed_mimes
                            )
                        except BadRequestError as exc:
                            per_file_errors.append(f"{entry.filename}: {exc.detail}")
                            skipped_count += 1
                            return None

                    mime_type: str = real_mime
                    if mime_type == "application/octet-stream":
                        guessed, _ = mimetypes.guess_type(safe_name)
                        mime_type = guessed or "application/octet-stream"

                    # Per-type size limit
                    try:
                        _check_per_type_size(mime_type, pf.size, config=config)
                    except BadRequestError as exc:
                        per_file_errors.append(f"{entry.filename}: {exc.detail}")
                        skipped_count += 1
                        return None

                    # Global storage limit
                    try:
                        await _check_storage_limit(pf.size, config=config)
                    except BadRequestError as exc:
                        per_file_errors.append(f"{entry.filename}: {exc.detail}")
                        skipped_count += 1
                        return None

                    # SVG safety check
                    if mime_type == "image/svg+xml":
                        try:
                            from app.core.file_security import check_svg_safety_stream

                            with pf.open("rb") as fh:
                                check_svg_safety_stream(fh, safe_name)
                        except SvgSecurityError as exc:
                            per_file_errors.append(f"{entry.filename}: SVG unsafe — {exc}")
                            skipped_count += 1
                            return None

                    quarantine_key = f"quarantine/{user_id}/{upload_id}/{safe_name}"

                    # Reserve quota slot
                    try:
                        await _check_pending_cap(
                            user_id,
                            redis,
                            privileged=privileged,
                            reserve_key=quarantine_key,
                        )
                    except BadRequestError:
                        per_file_errors.append(
                            f"{entry.filename}: upload quota exceeded, file skipped."
                        )
                        skipped_count += 1
                        return None

                    # Upload to quarantine
                    from app.config import settings as _settings

                    async with get_s3_client() as s3:
                        await s3.upload_file(  # type: ignore[call-arg]
                            Filename=str(pf.path),
                            Bucket=config.get("s3_bucket") or _settings.s3_bucket,
                            Key=quarantine_key,
                            ExtraArgs={"ContentType": mime_type},
                        )

                    await _create_upload_row(
                        upload_id=upload_id,
                        user_id=user_id,
                        quarantine_key=quarantine_key,
                        filename=safe_name,
                        mime_type=mime_type,
                        size_bytes=pf.size,
                    )

                    await _enqueue_processing(
                        user_id,
                        upload_id,
                        quarantine_key,
                        safe_name,
                        mime_type,
                        file_size=pf.size,
                    )

                    return BatchZipEntry(
                        filename=safe_name,
                        relative_path=entry.relative_path,
                        quarantine_key=quarantine_key,
                        upload_id=upload_id,
                        size=pf.size,
                        mime_type=mime_type,
                    )

                except BadRequestError:
                    raise
                except Exception:
                    logger.exception("Unexpected error processing zip entry %s", entry.filename)
                    per_file_errors.append(f"{entry.filename}: internal error, skipped.")
                    skipped_count += 1
                    return None

        task_results = await asyncio.gather(*[_process_one(e) for e in entries])
        results = [r for r in task_results if r is not None]

        return BatchZipResponse(
            files=results,
            skipped=skipped_count,
            errors=per_file_errors,
        )

    finally:
        await asyncio.to_thread(shutil.rmtree, tmp_dir, True)
