"""Backup and restore service.

Creates ZIP snapshots of the platform state:
  - DB tables: all 24 application tables in FK-safe insertion order
  - S3 prefixes: cas/, uploads/, thumbnails/, branding/

ZIP layout (v2):
  manifest.json          – summary + version
  s3_metadata.json       – per-object HTTP headers (ContentType, ContentEncoding, …)
  db/{table_name}.json   – rows for every table
  s3/{key}               – raw object bytes (gzip-encoded objects are NOT decompressed)

Version history:
  1.0  – original: 10 tables, 3 prefixes, no metadata sidecar, gzip decompressed
  2.0  – lossless:  24 tables, 4 prefixes, metadata sidecar, raw bytes preserved
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.storage.facade import (
    copy_object,
    delete_object,
    download_file_raw,
    get_object_headers,
    list_objects,
    upload_file,
    upload_file_multipart,
)

logger = logging.getLogger(__name__)

BACKUP_VERSION = "2.0"
BACKUP_PREFIXES = ("cas/", "uploads/", "thumbnails/", "branding/")
MAX_LOCAL_BACKUPS = 3
BACKUP_FILENAME_PREFIX = "backup_"

# All application tables in FK-safe insertion order.
# Rules:
#   • parent tables precede child tables
#   • self-referential tables come after all their non-self dependencies
#   • junction/audit tables that depend on two parents come last among those parents
_TABLE_INSERT_ORDER = [
    # ── no FK dependencies ───────────────────────────────────────────────────
    "users",
    "tags",
    "allowed_domains",
    "dead_letter_jobs",
    # ── depend only on users / tags ──────────────────────────────────────────
    "directories",  # self-ref: parent_id → topological sort on restore
    "notifications",  # FK: users
    "uploads",  # FK: none (standalone tracking table)
    # ── depend on directories (and optionally users) ─────────────────────────
    "materials",  # FK: directories, users; self-ref: parent_material_id
    "directory_tags",  # FK: directories, tags
    "directory_likes",  # FK: users, directories
    "directory_favourites",  # FK: users, directories
    # ── depend on materials ──────────────────────────────────────────────────
    "pull_requests",  # FK: materials; self-ref: reverts_pr_id / reverted_by_pr_id
    "material_tags",  # FK: materials, tags
    "material_likes",  # FK: users, materials
    "material_favourites",  # FK: users, materials
    "featured_items",  # FK: materials, directories, users
    "flags",  # FK: users (target_id is a polymorphic UUID, no FK constraint)
    "view_history",  # FK: users, materials
    "download_audit",  # FK: users, materials
    "comments",  # FK: users (standalone threaded comments, no material FK)
    # ── depend on materials + pull_requests ──────────────────────────────────
    "material_versions",  # FK: materials, pull_requests
    # ── depend on materials + material_versions ──────────────────────────────
    "annotations",  # FK: materials, material_versions, users; self-ref: parent_id / thread_root_id
    # ── depend on pull_requests + material_versions ──────────────────────────
    "pr_file_claims",  # FK: pull_requests, material_versions
    "pr_comments",  # FK: pull_requests; self-ref: parent_id
]
_TABLE_DELETE_ORDER = list(reversed(_TABLE_INSERT_ORDER))

# Self-referential FKs that require topological sort on restore:
#   table → (fk_column, pk_column)
_SELF_REF_FK: dict[str, tuple[str, str]] = {
    "directories": ("parent_id", "id"),
    "materials": ("parent_material_id", "id"),
    "annotations": ("thread_id", "id"),  # thread_id → self-referential thread root
    "pr_comments": ("parent_id", "id"),
}

# pull_request cross-refs are inserted as NULL then updated to avoid circular FK issues.
_PR_DEFERRED_COLS = ("reverts_pr_id", "reverted_by_pr_id")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
# SQLite stores UUIDs as 32-char hex without dashes.
_BARE_UUID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_ISO_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")

# Multipart threshold for restore uploads — objects larger than this are streamed
# via upload_file_multipart rather than buffered in RAM.
_RESTORE_MULTIPART_THRESHOLD = 5 * 1024 * 1024  # 5 MiB
_BACKUP_MANIFEST_MAX_BYTES = 1024 * 1024
_BACKUP_METADATA_MAX_BYTES = 64 * 1024 * 1024
_BACKUP_TABLE_MAX_BYTES = 128 * 1024 * 1024
_BACKUP_MAX_ENTRIES = 100_000
_BACKUP_MAX_COMPRESSION_RATIO = 1_000
_BACKUP_METADATA_HEADROOM_BYTES = 512 * 1024 * 1024


def backup_restore_max_bytes() -> int:
    """Bound backup input using the deployment's configured storage capacity."""
    return max(settings.max_storage_gb, 1) * 1024**3 + _BACKUP_METADATA_HEADROOM_BYTES


def _read_zip_entry_bounded(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise ValueError(f"Backup entry {info.filename!r} exceeds its size limit")
    with zf.open(info) as source:
        data = source.read(limit + 1)
    if len(data) > limit or len(data) != info.file_size:
        raise ValueError(f"Backup entry {info.filename!r} has an invalid expanded size")
    return data


def _validate_backup_archive(
    zip_path: Path,
) -> tuple[
    dict[str, Any], dict[str, list[dict[str, Any]]], list[str], dict[str, dict[str, str | None]]
]:
    """Validate and fully read a backup before any destructive restore step."""
    max_total_bytes = backup_restore_max_bytes()
    try:
        archive = zipfile.ZipFile(zip_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Uploaded backup is not a valid ZIP archive") from exc

    try:
        with archive as zf:
            entries = zf.infolist()
            if len(entries) > _BACKUP_MAX_ENTRIES:
                raise ValueError("Backup contains too many ZIP entries")

            by_name: dict[str, zipfile.ZipInfo] = {}
            total_declared = 0
            for info in entries:
                name = info.filename
                if (
                    not name
                    or name in by_name
                    or "\\" in name
                    or name.startswith("/")
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                ):
                    raise ValueError(f"Backup contains an unsafe or duplicate entry: {name!r}")
                if info.is_dir() or info.flag_bits & 0x1:
                    raise ValueError(f"Backup contains an unsupported ZIP entry: {name!r}")
                if info.compress_size == 0 and info.file_size > 0:
                    raise ValueError(f"Backup entry {name!r} has an invalid compressed size")
                if (
                    info.file_size >= 1024 * 1024
                    and info.compress_size
                    and info.file_size / info.compress_size > _BACKUP_MAX_COMPRESSION_RATIO
                ):
                    raise ValueError(f"Backup entry {name!r} has a suspicious compression ratio")
                total_declared += info.file_size
                if total_declared > max_total_bytes:
                    raise ValueError("Backup expands beyond the configured storage capacity")
                by_name[name] = info

            manifest_info = by_name.get("manifest.json")
            if manifest_info is None:
                raise ValueError("Backup is missing manifest.json")
            manifest = json.loads(
                _read_zip_entry_bounded(zf, manifest_info, _BACKUP_MANIFEST_MAX_BYTES)
            )
            if not isinstance(manifest, dict):
                raise ValueError("Backup manifest must be a JSON object")

            version = manifest.get("version", "1.0")
            if version not in ("1.0", "2.0"):
                raise ValueError(
                    f"Incompatible backup version {version!r} (supported: '1.0', '2.0')"
                )

            db_data: dict[str, list[dict[str, Any]]] = {}
            for table in _TABLE_INSERT_ORDER:
                name = f"db/{table}.json"
                table_info = by_name.get(name)
                rows = (
                    json.loads(_read_zip_entry_bounded(zf, table_info, _BACKUP_TABLE_MAX_BYTES))
                    if table_info is not None
                    else []
                )
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError(f"Backup table {table!r} must contain a JSON row array")
                db_data[table] = rows

            metadata_info = by_name.get("s3_metadata.json")
            raw_metadata = (
                json.loads(_read_zip_entry_bounded(zf, metadata_info, _BACKUP_METADATA_MAX_BYTES))
                if metadata_info is not None
                else {}
            )
            if not isinstance(raw_metadata, dict):
                raise ValueError("Backup object metadata must be a JSON object")
            s3_metadata: dict[str, dict[str, str | None]] = {}
            for key, metadata in raw_metadata.items():
                if not isinstance(key, str) or not isinstance(metadata, dict):
                    raise ValueError("Backup object metadata has an invalid shape")
                if any(value is not None and not isinstance(value, str) for value in metadata.values()):
                    raise ValueError(f"Backup object metadata for {key!r} is invalid")
                s3_metadata[key] = metadata

            s3_entries: list[str] = []
            object_hashes = manifest.get("s3_objects", {})
            if not isinstance(object_hashes, dict):
                raise ValueError("Backup object integrity manifest is invalid")
            for name, info in by_name.items():
                if not name.startswith("s3/"):
                    continue
                key = name[3:]
                if not key or not key.startswith(BACKUP_PREFIXES):
                    raise ValueError(f"Backup contains an object outside managed prefixes: {key!r}")
                digest = hashlib.sha256()
                actual_size = 0
                with zf.open(info) as source:
                    while chunk := source.read(1024 * 1024):
                        actual_size += len(chunk)
                        if actual_size > info.file_size:
                            raise ValueError(f"Backup object {key!r} expanded beyond its declared size")
                        digest.update(chunk)
                if actual_size != info.file_size:
                    raise ValueError(f"Backup object {key!r} was truncated")
                expected = object_hashes.get(key)
                if expected is not None and (
                    not isinstance(expected, dict)
                    or expected.get("size") != actual_size
                    or expected.get("sha256") != digest.hexdigest()
                ):
                    raise ValueError(f"Backup object {key!r} failed its integrity check")
                s3_entries.append(name)

            declared_count = manifest.get("s3_object_count")
            if not isinstance(declared_count, int) or declared_count != len(s3_entries):
                raise ValueError("Backup object count does not match its manifest")

            allowed_names = {
                "manifest.json",
                "s3_metadata.json",
                *(f"db/{table}.json" for table in _TABLE_INSERT_ORDER),
                *s3_entries,
            }
            unexpected = set(by_name) - allowed_names
            if unexpected:
                raise ValueError(f"Backup contains unexpected entries: {sorted(unexpected)[:3]!r}")

            return manifest, db_data, s3_entries, s3_metadata
    except (json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("Backup contains corrupt or invalid data") from exc


# ── Serialization helpers ─────────────────────────────────────────────────────


def _serialize_value(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, str) and _BARE_UUID_RE.match(v):
        # SQLite stores UUID columns as 32-char hex without dashes.
        try:
            return str(uuid.UUID(v))
        except ValueError:
            pass
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _serialize_value(v) for k, v in row.items()}


def _deserialize_value(v: Any) -> Any:
    """Convert JSON strings to datetime where appropriate.

    UUID strings are intentionally kept as strings: SQLite doesn't accept
    uuid.UUID objects in bound parameters, and PostgreSQL accepts string UUIDs
    via implicit text→uuid casting in parameterized queries.
    """
    if not isinstance(v, str):
        return v
    if _ISO_DT_RE.match(v):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            pass
    return v


def _deserialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _deserialize_value(v) for k, v in row.items()}


# ── DB dump ───────────────────────────────────────────────────────────────────


async def _dump_table(db: AsyncSession, table_name: str) -> list[dict[str, Any]]:
    result = await db.execute(text(f'SELECT * FROM "{table_name}"'))  # noqa: S608
    return [_serialize_row(dict(row._mapping)) for row in result]


# ── DB restore helpers ────────────────────────────────────────────────────────


def _topological_sort(rows: list[dict[str, Any]], pk_col: str, fk_col: str) -> list[dict[str, Any]]:
    """Sort rows so parents precede children (handles self-referential FKs)."""
    by_id: dict[str, dict[str, Any]] = {str(r[pk_col]): r for r in rows}
    visited: set[str] = set()
    result: list[dict[str, Any]] = []

    def visit(row_id: str) -> None:
        if row_id in visited:
            return
        visited.add(row_id)
        row = by_id.get(row_id)
        if row is None:
            return
        parent_id = row.get(fk_col)
        if parent_id is not None and str(parent_id) in by_id:
            visit(str(parent_id))
        result.append(row)

    for row in rows:
        visit(str(row[pk_col]))
    return result


def _coerce_for_insert(v: Any) -> Any:
    """Ensure value is bindable by both SQLite and asyncpg.

    dict/list → JSON string (SQLite stores as TEXT; PostgreSQL casts TEXT→JSONB implicitly).
    All other types pass through unchanged.
    """
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def _build_insert(table_name: str, row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (sql_text, params_dict) for a single-row INSERT."""
    cols = ", ".join(f'"{k}"' for k in row)
    # Prefix param names with "p_" to avoid collisions with SQL reserved words.
    param_map = {k: f"p_{k}" for k in row}
    placeholders = ", ".join(f":{param_map[k]}" for k in row)
    params = {param_map[k]: _coerce_for_insert(v) for k, v in row.items()}
    return f'INSERT INTO "{table_name}" ({cols}) VALUES ({placeholders})', params


async def _restore_table(db: AsyncSession, table_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    deserialized = [_deserialize_row(r) for r in rows]

    if table_name in _SELF_REF_FK:
        fk_col, pk_col = _SELF_REF_FK[table_name]
        deserialized = _topological_sort(deserialized, pk_col, fk_col)

    if table_name == "pull_requests":
        # Insert PRs with circular self-refs nulled, update them after.
        deferred: dict[str, dict[str, Any]] = {}
        cleaned: list[dict[str, Any]] = []
        for row in deserialized:
            refs = {c: row[c] for c in _PR_DEFERRED_COLS if row.get(c) is not None}
            if refs:
                deferred[str(row["id"])] = refs
            cleaned.append({k: (None if k in _PR_DEFERRED_COLS else v) for k, v in row.items()})

        for row in cleaned:
            sql, params = _build_insert(table_name, row)
            await db.execute(text(sql), params)

        for pr_id, refs in deferred.items():
            set_clause = ", ".join(f'"{c}" = :p_{c}' for c in refs)
            up_params: dict[str, Any] = {f"p_{c}": v for c, v in refs.items()}
            up_params["p_id"] = pr_id  # string UUID — both SQLite and PostgreSQL accept this
            await db.execute(
                text(f'UPDATE "pull_requests" SET {set_clause} WHERE "id" = :p_id'),
                up_params,
            )
        return

    for row in deserialized:
        sql, params = _build_insert(table_name, row)
        await db.execute(text(sql), params)


# ── Public API ────────────────────────────────────────────────────────────────


async def create_backup_zip(db: AsyncSession, dest_path: Path) -> dict[str, Any]:
    """Create a lossless backup ZIP at dest_path. Returns the manifest dict.

    S3 objects are downloaded via :func:`download_file_raw` so gzip-encoded
    objects are stored as-is (no silent decompression).  Per-object metadata
    (ContentType, ContentEncoding, ContentDisposition, CacheControl) is written
    to ``s3_metadata.json`` inside the ZIP so restore can reproduce the exact
    HTTP headers.
    """
    db_data: dict[str, list[dict[str, Any]]] = {}
    for table_name in _TABLE_INSERT_ORDER:
        # A backup that silently omits a table is not a backup. Production
        # startup already requires current migrations, so fail closed here.
        db_data[table_name] = await _dump_table(db, table_name)

    s3_keys: list[str] = []
    for prefix in BACKUP_PREFIXES:
        async for obj in list_objects(prefix):
            s3_keys.append(obj["Key"])
    s3_keys = sorted(set(s3_keys))

    # Collect per-object metadata and raw bytes concurrently.
    # We use a semaphore to avoid opening hundreds of S3 connections at once.
    _sem = asyncio.Semaphore(10)

    async def _fetch_one(
        key: str, local: Path
    ) -> tuple[dict[str, str | None], dict[str, str | int]]:
        async with _sem:
            await download_file_raw(key, local)
            metadata = await get_object_headers(key)

            def _integrity() -> dict[str, str | int]:
                digest = hashlib.sha256()
                size = 0
                with open(local, "rb") as source:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                return {"size": size, "sha256": digest.hexdigest()}

            return metadata, await asyncio.to_thread(_integrity)

    manifest: dict[str, Any] = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "tables": _TABLE_INSERT_ORDER,
        "s3_prefixes": list(BACKUP_PREFIXES),
        "s3_object_count": len(s3_keys),
        "db_row_counts": {t: len(rows) for t, rows in db_data.items()},
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        # Download all objects and their metadata.
        s3_local: dict[str, Path] = {}
        tasks: dict[
            str, asyncio.Task[tuple[dict[str, str | None], dict[str, str | int]]]
        ] = {}
        for index, key in enumerate(s3_keys):
            # Storage keys are not filesystem paths. An ordinal prevents both
            # traversal and collisions such as ``a/b`` versus ``a__b``.
            local = tmp / f"object_{index}"
            s3_local[key] = local
            tasks[key] = asyncio.create_task(_fetch_one(key, local))

        s3_metadata: dict[str, dict[str, str | None]] = {}
        s3_integrity: dict[str, dict[str, str | int]] = {}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks, results, strict=True):
            if isinstance(result, BaseException):
                raise RuntimeError(f"Backup could not capture object {key!r}") from result
            s3_metadata[key], s3_integrity[key] = result
        manifest["s3_objects"] = s3_integrity

        def _write() -> None:
            with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                zf.writestr("s3_metadata.json", json.dumps(s3_metadata, indent=2))
                for tbl, rows in db_data.items():
                    zf.writestr(f"db/{tbl}.json", json.dumps(rows))
                for key, local in s3_local.items():
                    if local.exists():
                        zf.write(str(local), f"s3/{key}")

        await asyncio.to_thread(_write)

    return manifest


async def restore_from_zip_path(db: AsyncSession, zip_path: Path) -> dict[str, Any]:
    """Full-replacement restore from a local ZIP file. Returns the manifest.

    Supports both v1.0 and v2.0 backup archives.  v1 archives lack the
    ``s3_metadata.json`` sidecar; objects are restored with safe defaults
    (``application/octet-stream``, no encoding override).

    S3 objects above 5 MiB are restored via multipart upload to avoid buffering
    the entire object in RAM.
    """

    manifest, db_data, s3_entry_names, s3_metadata = await asyncio.to_thread(
        _validate_backup_archive, zip_path
    )

    version = manifest.get("version", "1.0")
    if version not in ("1.0", "2.0"):
        raise ValueError(f"Incompatible backup version {version!r} (supported: '1.0', '2.0')")
    if version == "1.0":
        logger.warning(
            "Restoring a v1.0 backup: S3 object metadata (Content-Type, Content-Encoding, "
            "Content-Disposition) will use safe defaults. Re-backup after restore to capture "
            "full metadata."
        )

    # Wipe existing DB rows (reverse FK order)
    for tbl in _TABLE_DELETE_ORDER:
        # Never continue from a partial wipe. PostgreSQL also marks the entire
        # transaction failed after a statement error, so "skip and continue"
        # cannot provide compatibility and only hides destructive failures.
        await db.execute(text(f'DELETE FROM "{tbl}"'))

    # Restore DB rows (forward FK order)
    for tbl in _TABLE_INSERT_ORDER:
        await _restore_table(db, tbl, db_data.get(tbl, []))

    await db.flush()

    # Snapshot every managed object before changing storage. S3 has no
    # multi-object transaction, so these server-side copies are the rollback
    # journal for upload/delete failures during full replacement.
    existing_keys: list[str] = []
    for prefix in BACKUP_PREFIXES:
        async for obj in list_objects(prefix):
            existing_keys.append(obj["Key"])
    existing_keys = sorted(set(existing_keys))

    rollback_prefix = f"restore-rollback/{uuid.uuid4()}/"
    rollback_keys = {
        key: f"{rollback_prefix}{index}" for index, key in enumerate(existing_keys)
    }
    captured_rollback_keys: list[str] = []
    try:
        for key, rollback_key in rollback_keys.items():
            await copy_object(key, rollback_key)
            captured_rollback_keys.append(rollback_key)
    except Exception:
        for rollback_key in captured_rollback_keys:
            try:
                await delete_object(rollback_key)
            except Exception:
                logger.exception("Restore: failed to clean incomplete rollback object %r", rollback_key)
        raise

    # Restore S3 objects.  Objects ≥ 5 MiB are streamed via a temp file to
    # avoid loading the full object into RAM.
    restored_keys: set[str] = set()
    attempted_keys: set[str] = set()
    rollback_complete = False
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)

            for index, entry_name in enumerate(s3_entry_names):
                key = entry_name[3:]  # strip leading "s3/"
                meta = s3_metadata.get(key, {})

                content_type: str = meta.get("content_type") or "application/octet-stream"
                content_encoding: str | None = meta.get("content_encoding")
                content_disposition: str | None = meta.get("content_disposition") or "attachment"

                def _extract_entry(
                    name: str = entry_name, dest: Path = tmp / f"object_{index}"
                ) -> tuple[Path, int]:
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        info = zf.getinfo(name)
                        file_size = info.file_size
                        with zf.open(name) as src, open(dest, "wb") as dst:
                            while chunk := src.read(64 * 1024):
                                dst.write(chunk)
                    return dest, file_size

                local_path, file_size = await asyncio.to_thread(_extract_entry)

                try:
                    # Record before I/O so a lost-success response for a new key
                    # is still removed during rollback.
                    attempted_keys.add(key)
                    if file_size >= _RESTORE_MULTIPART_THRESHOLD:
                        await upload_file_multipart(
                            local_path,
                            key,
                            content_type=content_type,
                            content_encoding=content_encoding,
                            content_disposition=content_disposition,
                        )
                    else:
                        data = await asyncio.to_thread(local_path.read_bytes)
                        await upload_file(
                            data,
                            key,
                            content_type=content_type,
                            content_encoding=content_encoding,
                            content_disposition=content_disposition,
                        )
                    restored_keys.add(key)
                finally:
                    local_path.unlink(missing_ok=True)

        for stale_key in set(existing_keys) - restored_keys:
            await delete_object(stale_key)
        rollback_complete = True
    except Exception as restore_error:
        rollback_errors: list[Exception] = []
        for key, rollback_key in rollback_keys.items():
            try:
                await copy_object(rollback_key, key)
            except Exception as exc:
                rollback_errors.append(exc)
                logger.exception("Restore: failed to roll back object %r", key)
        for new_key in attempted_keys - set(existing_keys):
            try:
                await delete_object(new_key)
            except Exception as exc:
                rollback_errors.append(exc)
                logger.exception("Restore: failed to remove newly restored object %r", new_key)
        if rollback_errors:
            raise RuntimeError(
                "Object restore failed and storage rollback was incomplete; "
                f"rollback data remains under {rollback_prefix!r}"
            ) from restore_error
        rollback_complete = True
        raise
    finally:
        if rollback_complete:
            for rollback_key in rollback_keys.values():
                try:
                    await delete_object(rollback_key)
                except Exception:
                    # Cleanup failure leaks recovery data but never invalidates
                    # an otherwise completed restore or successful rollback.
                    logger.exception("Restore: failed to clean rollback object %r", rollback_key)

    return manifest


# ── Local backup management ───────────────────────────────────────────────────


def list_local_backups(backup_dir: Path) -> list[dict[str, Any]]:
    """Return metadata for all local backups, sorted oldest-first."""
    backups: list[dict[str, Any]] = []
    for f in backup_dir.glob(f"{BACKUP_FILENAME_PREFIX}*.zip"):
        stat = f.stat()
        backups.append(
            {
                "id": f.stem,
                "filename": f.name,
                "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                "size_bytes": stat.st_size,
            }
        )
    return sorted(backups, key=lambda x: x["filename"])


def enforce_backup_rotation(backup_dir: Path, max_count: int = MAX_LOCAL_BACKUPS) -> list[str]:
    """Delete oldest local backups until at most max_count remain."""
    backups = list_local_backups(backup_dir)
    deleted: list[str] = []
    while len(backups) > max_count:
        oldest = backups.pop(0)
        (backup_dir / oldest["filename"]).unlink(missing_ok=True)
        deleted.append(oldest["filename"])
        logger.info("Backup rotation: removed %s", oldest["filename"])
    return deleted


def backup_filename() -> str:
    """Generate a timestamped backup filename (stem only)."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{BACKUP_FILENAME_PREFIX}{ts}"
