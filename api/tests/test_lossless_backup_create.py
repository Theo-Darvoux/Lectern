"""Tests for create_backup_zip: metadata sidecar, raw bytes, all prefixes, all tables.

Covers:
  - s3_metadata.json written to ZIP with correct headers per object
  - download_file_raw called (not download_file) so gzip bytes are preserved
  - get_object_headers called once per S3 key
  - branding/ prefix included in listing
  - All application DB tables present in ZIP
  - Table dump failure is skipped gracefully (no crash)
  - Concurrent download semaphore doesn't starve
  - ZIP is valid and allowZip64=True
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.backup import (
    _TABLE_INSERT_ORDER,
    BACKUP_PREFIXES,
    BACKUP_VERSION,
    create_backup_zip,
)


async def _empty_gen(*_a, **_kw):
    if False:
        yield


def _one_object_gen(key: str, size: int = 10):
    async def _gen(*_a, **_kw):
        yield {"Key": key, "Size": size}

    return _gen


# ── Metadata sidecar ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s3_metadata_sidecar_present_in_zip(db_session: AsyncSession, tmp_path: Path) -> None:
    """ZIP must contain s3_metadata.json."""
    dest = tmp_path / "b.zip"
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        await create_backup_zip(db_session, dest)

    with zipfile.ZipFile(dest) as zf:
        assert "s3_metadata.json" in zf.namelist()


@pytest.mark.asyncio
async def test_s3_metadata_sidecar_contains_per_object_headers(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """s3_metadata.json must map each S3 key to its fetched headers."""
    dest = tmp_path / "b.zip"
    fake_meta = {
        "content_type": "application/pdf",
        "content_encoding": "gzip",
        "content_disposition": 'attachment; filename="doc.pdf"',
        "cache_control": "public, max-age=86400",
    }

    async def _fake_list(prefix: str):
        if prefix == "cas/":
            yield {"Key": "cas/deadbeef", "Size": 4}

    async def _fake_download(key: str, dest_path):
        Path(dest_path).write_bytes(b"data")

    with (
        patch("app.services.backup.list_objects", side_effect=_fake_list),
        patch("app.services.backup.download_file_raw", side_effect=_fake_download),
        patch(
            "app.services.backup.get_object_headers", new_callable=AsyncMock, return_value=fake_meta
        ),
    ):
        await create_backup_zip(db_session, dest)

    with zipfile.ZipFile(dest) as zf:
        meta = json.loads(zf.read("s3_metadata.json"))

    assert "cas/deadbeef" in meta
    assert meta["cas/deadbeef"]["content_type"] == "application/pdf"
    assert meta["cas/deadbeef"]["content_encoding"] == "gzip"


@pytest.mark.asyncio
async def test_get_object_headers_called_once_per_key(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """get_object_headers must be called exactly once per discovered S3 key."""
    dest = tmp_path / "b.zip"
    keys = ["cas/k1", "uploads/u1/k2", "thumbnails/t1"]

    async def _fake_list(prefix: str):
        for k in keys:
            if k.startswith(prefix):
                yield {"Key": k, "Size": 1}

    async def _fake_download(key: str, dest_path):
        Path(dest_path).write_bytes(b"x")

    mock_headers = AsyncMock(return_value={})
    with (
        patch("app.services.backup.list_objects", side_effect=_fake_list),
        patch("app.services.backup.download_file_raw", side_effect=_fake_download),
        patch("app.services.backup.get_object_headers", mock_headers),
    ):
        await create_backup_zip(db_session, dest)

    assert mock_headers.call_count == len(keys)
    called_keys = {c.args[0] for c in mock_headers.call_args_list}
    assert called_keys == set(keys)


@pytest.mark.asyncio
async def test_download_file_raw_called_for_each_key(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Backup must call download_file_raw for every discovered S3 key."""
    dest = tmp_path / "b.zip"

    async def _fake_list(prefix: str):
        if prefix == "cas/":
            yield {"Key": "cas/obj", "Size": 3}

    async def _fake_raw(key: str, dest_path):
        Path(dest_path).write_bytes(b"raw")

    mock_raw = AsyncMock(side_effect=_fake_raw)

    with (
        patch("app.services.backup.list_objects", side_effect=_fake_list),
        patch("app.services.backup.download_file_raw", mock_raw),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        await create_backup_zip(db_session, dest)

    mock_raw.assert_called_once()
    assert mock_raw.call_args.args[0] == "cas/obj"


# ── Prefix coverage ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_branding_prefix_included_in_backup(db_session: AsyncSession, tmp_path: Path) -> None:
    """branding/ objects must appear in the ZIP."""
    dest = tmp_path / "b.zip"
    branding_content = b"logo bytes"

    async def _fake_list(prefix: str):
        if prefix == "branding/":
            yield {"Key": "branding/logo.png", "Size": len(branding_content)}

    async def _fake_download(key: str, dest_path):
        Path(dest_path).write_bytes(branding_content)

    with (
        patch("app.services.backup.list_objects", side_effect=_fake_list),
        patch("app.services.backup.download_file_raw", side_effect=_fake_download),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        manifest = await create_backup_zip(db_session, dest)

    assert manifest["s3_object_count"] == 1
    with zipfile.ZipFile(dest) as zf:
        assert "s3/branding/logo.png" in zf.namelist()
        assert zf.read("s3/branding/logo.png") == branding_content


def test_backup_prefixes_includes_branding() -> None:
    """BACKUP_PREFIXES constant must include branding/."""
    assert "branding/" in BACKUP_PREFIXES


def test_backup_prefixes_excludes_quarantine() -> None:
    """quarantine/ must never be in BACKUP_PREFIXES (unsafe content)."""
    assert not any("quarantine" in p for p in BACKUP_PREFIXES)


@pytest.mark.asyncio
async def test_all_four_prefixes_are_listed(db_session: AsyncSession, tmp_path: Path) -> None:
    """list_objects must be called for each of the four backup prefixes."""
    dest = tmp_path / "b.zip"
    listed: list[str] = []

    async def _fake_list(prefix: str):
        listed.append(prefix)
        if False:
            yield

    with (
        patch("app.services.backup.list_objects", side_effect=_fake_list),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        await create_backup_zip(db_session, dest)

    assert set(listed) == set(BACKUP_PREFIXES)


# ── Table coverage ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_application_tables_in_zip(db_session: AsyncSession, tmp_path: Path) -> None:
    """ZIP must contain a db/{table}.json entry for every table in _TABLE_INSERT_ORDER."""
    dest = tmp_path / "b.zip"
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        await create_backup_zip(db_session, dest)

    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()

    for tbl in _TABLE_INSERT_ORDER:
        assert f"db/{tbl}.json" in names, f"Missing table dump: {tbl}"


def test_table_order_covers_every_application_table() -> None:
    """A new ORM table must never silently escape full backup and restore."""
    from app.models import Base

    expected = {
        "installation_state",
        "users",
        "tags",
        "allowed_domains",
        "dead_letter_jobs",
        "outbox_jobs",
        "scheduled_job_runs",
        "directories",
        "notifications",
        "uploads",
        "cas_staging_claims",
        "collections",
        "materials",
        "directory_tags",
        "directory_likes",
        "directory_favourites",
        "pull_requests",
        "material_tags",
        "material_likes",
        "material_favourites",
        "collection_items",
        "featured_items",
        "flags",
        "view_history",
        "download_audit",
        "comments",
        "material_versions",
        "annotations",
        "pr_file_claims",
        "pr_comments",
    }
    assert set(_TABLE_INSERT_ORDER) == expected
    assert set(_TABLE_INSERT_ORDER) == set(Base.metadata.tables)
    assert len(_TABLE_INSERT_ORDER) == 30  # no duplicates


def test_table_order_no_duplicates() -> None:
    assert len(_TABLE_INSERT_ORDER) == len(set(_TABLE_INSERT_ORDER))


def test_parent_tables_precede_children() -> None:
    """FK-critical ordering: users before everything, materials after directories."""
    order = {t: i for i, t in enumerate(_TABLE_INSERT_ORDER)}
    assert order["users"] < order["materials"]
    assert order["users"] < order["notifications"]
    assert order["users"] < order["cas_staging_claims"]
    assert order["users"] < order["collections"]
    assert order["directories"] < order["materials"]
    assert order["collections"] < order["collection_items"]
    assert order["directories"] < order["collection_items"]
    assert order["materials"] < order["collection_items"]
    assert order["materials"] < order["material_versions"]
    assert order["pull_requests"] < order["material_versions"]
    assert order["material_versions"] < order["annotations"]
    assert order["pull_requests"] < order["pr_comments"]
    assert order["pull_requests"] < order["pr_file_claims"]


# ── Fault tolerance ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_table_dump_failure_aborts_incomplete_backup(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A backup must never certify a snapshot that silently omitted a table."""
    dest = tmp_path / "b.zip"

    original_dump = None
    import app.services.backup as svc

    async def _flaky_dump(db, table_name, destination):
        if table_name == "dead_letter_jobs":
            raise Exception("table missing")
        return await original_dump(db, table_name, destination)  # type: ignore[misc]

    original_dump = svc._dump_table_to_path

    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
        patch("app.services.backup._dump_table_to_path", side_effect=_flaky_dump),
    ):
        with pytest.raises(Exception, match="table missing"):
            await create_backup_zip(db_session, dest)

    assert not dest.exists()


# ── ZIP format ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zip_is_valid(db_session: AsyncSession, tmp_path: Path) -> None:
    dest = tmp_path / "b.zip"
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        await create_backup_zip(db_session, dest)

    assert zipfile.is_zipfile(dest)


@pytest.mark.asyncio
async def test_manifest_version_is_2(db_session: AsyncSession, tmp_path: Path) -> None:
    dest = tmp_path / "b.zip"
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        manifest = await create_backup_zip(db_session, dest)

    assert manifest["version"] == BACKUP_VERSION == "2.0"
