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

from app.core.storage import (
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
        try:
            db_data[table_name] = await _dump_table(db, table_name)
        except Exception:
            # Table may not exist yet (e.g. pre-migration instance). Log and skip.
            logger.warning("Backup: could not dump table %r — skipping", table_name)
            db_data[table_name] = []

    s3_keys: list[str] = []
    for prefix in BACKUP_PREFIXES:
        async for obj in list_objects(prefix):
            s3_keys.append(obj["Key"])

    # Collect per-object metadata and raw bytes concurrently.
    # We use a semaphore to avoid opening hundreds of S3 connections at once.
    _sem = asyncio.Semaphore(10)

    async def _fetch_one(key: str, local: Path) -> dict[str, str | None]:
        async with _sem:
            await download_file_raw(key, local)
            return await get_object_headers(key)

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
        tasks: dict[str, asyncio.Task[dict[str, str | None]]] = {}
        for key in s3_keys:
            safe_name = key.replace("/", "__")
            local = tmp / safe_name
            s3_local[key] = local
            tasks[key] = asyncio.create_task(_fetch_one(key, local))

        s3_metadata: dict[str, dict[str, str | None]] = {}
        for key, task in tasks.items():
            try:
                s3_metadata[key] = await task
            except Exception as exc:
                logger.warning("Backup: failed to fetch metadata for %r: %s", key, exc)
                s3_metadata[key] = {}

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

    def _read_metadata() -> tuple[
        dict[str, Any], dict[str, list[dict[str, Any]]], list[str], dict[str, dict[str, str | None]]
    ]:
        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))

            db_data: dict[str, list[dict[str, Any]]] = {}
            for tbl in _TABLE_INSERT_ORDER:
                entry = f"db/{tbl}.json"
                db_data[tbl] = json.loads(zf.read(entry)) if entry in namelist else []

            s3_entries = [n for n in namelist if n.startswith("s3/")]

            # v2 backups include per-object metadata; v1 backups do not.
            if "s3_metadata.json" in namelist:
                s3_meta: dict[str, dict[str, str | None]] = json.loads(zf.read("s3_metadata.json"))
            else:
                s3_meta = {}

        return manifest, db_data, s3_entries, s3_meta

    manifest, db_data, s3_entry_names, s3_metadata = await asyncio.to_thread(_read_metadata)

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
        try:
            await db.execute(text(f'DELETE FROM "{tbl}"'))
        except Exception:
            logger.warning("Restore: could not truncate table %r — skipping", tbl)

    # Restore DB rows (forward FK order)
    for tbl in _TABLE_INSERT_ORDER:
        await _restore_table(db, tbl, db_data.get(tbl, []))

    await db.flush()

    # Wipe existing S3 objects in all backup prefixes
    for prefix in BACKUP_PREFIXES:
        async for obj in list_objects(prefix):
            await delete_object(obj["Key"])

    # Restore S3 objects.  Objects ≥ 5 MiB are streamed via a temp file to
    # avoid loading the full object into RAM.
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        for entry_name in s3_entry_names:
            key = entry_name[3:]  # strip leading "s3/"
            meta = s3_metadata.get(key, {})

            content_type: str = meta.get("content_type") or "application/octet-stream"
            content_encoding: str | None = meta.get("content_encoding")
            content_disposition: str | None = meta.get("content_disposition") or "attachment"

            def _extract_entry(
                name: str = entry_name, dest: Path = tmp / key.replace("/", "__")
            ) -> tuple[Path, int]:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    info = zf.getinfo(name)
                    file_size = info.file_size
                    with zf.open(name) as src, open(dest, "wb") as dst:
                        while True:
                            chunk = src.read(64 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                return dest, file_size

            local_path, file_size = await asyncio.to_thread(_extract_entry)

            try:
                if file_size >= _RESTORE_MULTIPART_THRESHOLD:
                    # Large object — stream via multipart to avoid RAM spike.
                    await upload_file_multipart(
                        local_path,
                        key,
                        content_type=content_type,
                        content_encoding=content_encoding,
                        content_disposition=content_disposition,
                    )
                else:
                    # Small object — single PUT.
                    data = await asyncio.to_thread(local_path.read_bytes)
                    await upload_file(
                        data,
                        key,
                        content_type=content_type,
                        content_encoding=content_encoding,
                        content_disposition=content_disposition,
                    )
            finally:
                local_path.unlink(missing_ok=True)

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
