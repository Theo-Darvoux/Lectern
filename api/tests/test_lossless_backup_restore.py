"""Tests for restore_from_zip_path: metadata applied on upload, v1 compat,
large-object streaming, missing sidecar keys, branding prefix wipe/restore.

Covers:
  - content_type / content_encoding / content_disposition from sidecar → upload call
  - Missing key in sidecar → safe defaults (application/octet-stream, no encoding)
  - v1.0 backup (no sidecar) → warning logged, safe defaults
  - v1.0 backup accepted (no ValueError)
  - v99 backup → ValueError
  - Large object (≥ 5 MiB in ZIP) → upload_file_multipart called
  - Small object (< 5 MiB) → upload_file called
  - branding/ prefix wiped before restore
  - branding/ object re-uploaded on restore
  - Truncate failure skipped gracefully
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.backup import (
    _RESTORE_MULTIPART_THRESHOLD,
    _TABLE_INSERT_ORDER,
    BACKUP_PREFIXES,
    BACKUP_VERSION,
    restore_from_zip_path,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_zip(
    tmp_path: Path,
    *,
    version: str = BACKUP_VERSION,
    s3_entries: dict[str, bytes] | None = None,
    s3_metadata: dict | None = None,
    include_metadata_sidecar: bool = True,
) -> Path:
    dest = tmp_path / "backup.zip"
    s3_entries_dict = s3_entries or {}
    s3_objects = {
        key: {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for key, data in s3_entries_dict.items()
    }
    manifest = {
        "version": version,
        "created_at": "2026-06-05T00:00:00+00:00",
        "tables": _TABLE_INSERT_ORDER,
        "s3_prefixes": list(BACKUP_PREFIXES),
        "s3_object_count": len(s3_entries_dict),
        "s3_objects": s3_objects,
        "db_row_counts": {},
    }

    with zipfile.ZipFile(dest, "w", allowZip64=True) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        if include_metadata_sidecar and s3_metadata is not None:
            zf.writestr("s3_metadata.json", json.dumps(s3_metadata))
        for tbl in _TABLE_INSERT_ORDER:
            zf.writestr(f"db/{tbl}.json", "[]")
        for key, data in s3_entries_dict.items():
            zf.writestr(f"s3/{key}", data)

    return dest


async def _empty_gen(*_a, **_kw):
    if False:
        yield


# ── Metadata applied on upload ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_applies_content_type_from_sidecar(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """upload_file must receive the content_type from s3_metadata.json."""
    zip_path = _make_zip(
        tmp_path,
        s3_entries={"cas/abc": b"bytes"},
        s3_metadata={
            "cas/abc": {
                "content_type": "application/pdf",
                "content_encoding": None,
                "content_disposition": "attachment",
                "cache_control": None,
            }
        },
    )
    upload_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    upload_mock.assert_called_once()
    kwargs = upload_mock.call_args
    assert kwargs[1]["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_restore_applies_content_encoding_from_sidecar(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """upload_file must receive content_encoding=gzip so the gzip bytes are tagged correctly."""
    zip_path = _make_zip(
        tmp_path,
        s3_entries={"cas/gz": b"\x1f\x8b fake gzip"},
        s3_metadata={
            "cas/gz": {
                "content_type": "application/octet-stream",
                "content_encoding": "gzip",
                "content_disposition": "attachment",
                "cache_control": None,
            }
        },
    )
    upload_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    assert upload_mock.call_args[1]["content_encoding"] == "gzip"


@pytest.mark.asyncio
async def test_restore_applies_content_disposition_from_sidecar(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    zip_path = _make_zip(
        tmp_path,
        s3_entries={"uploads/u1/f": b"data"},
        s3_metadata={
            "uploads/u1/f": {
                "content_type": "image/png",
                "content_encoding": None,
                "content_disposition": 'attachment; filename="img.png"',
                "cache_control": None,
            }
        },
    )
    upload_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    assert upload_mock.call_args[1]["content_disposition"] == 'attachment; filename="img.png"'


@pytest.mark.asyncio
async def test_restore_missing_key_in_sidecar_uses_defaults(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Key present in ZIP but absent from sidecar must fall back to safe defaults."""
    zip_path = _make_zip(
        tmp_path,
        s3_entries={"cas/unknown": b"data"},
        s3_metadata={},  # sidecar present but key absent
    )
    upload_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    kw = upload_mock.call_args[1]
    assert kw["content_type"] == "application/octet-stream"
    assert kw["content_encoding"] is None


# ── v1 / v2 compatibility ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_v1_backup_accepted(db_session: AsyncSession, tmp_path: Path) -> None:
    """v1.0 backups must be accepted (no ValueError)."""
    zip_path = _make_zip(tmp_path, version="1.0", include_metadata_sidecar=False)
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        result = await restore_from_zip_path(db_session, zip_path)

    assert result["version"] == "1.0"


@pytest.mark.asyncio
async def test_restore_v1_backup_logs_warning(db_session: AsyncSession, tmp_path: Path) -> None:
    """v1.0 restore must emit a warning about missing metadata."""
    zip_path = _make_zip(tmp_path, version="1.0", include_metadata_sidecar=False)
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
        patch("app.services.backup.logger") as mock_log,
    ):
        await restore_from_zip_path(db_session, zip_path)

    assert mock_log.warning.called
    warn_msg = str(mock_log.warning.call_args_list)
    assert "1.0" in warn_msg or "v1" in warn_msg.lower() or "metadata" in warn_msg.lower()


@pytest.mark.asyncio
async def test_restore_v1_backup_uses_safe_defaults(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """v1 restore with an S3 entry must fall back to application/octet-stream, no encoding."""
    zip_path = _make_zip(
        tmp_path,
        version="1.0",
        s3_entries={"cas/obj": b"data"},
        include_metadata_sidecar=False,
    )
    upload_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    kw = upload_mock.call_args[1]
    assert kw["content_type"] == "application/octet-stream"
    assert kw["content_encoding"] is None


@pytest.mark.asyncio
async def test_restore_v99_rejected(db_session: AsyncSession, tmp_path: Path) -> None:
    """Unknown version must raise ValueError."""
    zip_path = _make_zip(tmp_path, version="99.0")
    with pytest.raises(ValueError, match="Incompatible"):
        with (
            patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
            patch("app.services.backup.delete_object", new_callable=AsyncMock),
            patch("app.services.backup.upload_file", new_callable=AsyncMock),
        ):
            await restore_from_zip_path(db_session, zip_path)


# ── Large-object streaming ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_large_object_uses_multipart(db_session: AsyncSession, tmp_path: Path) -> None:
    """Objects ≥ RESTORE_MULTIPART_THRESHOLD bytes must use upload_file_multipart."""
    large_data = b"x" * _RESTORE_MULTIPART_THRESHOLD  # exactly at threshold
    zip_path = _make_zip(
        tmp_path,
        s3_entries={"cas/large": large_data},
        s3_metadata={
            "cas/large": {
                "content_type": "application/octet-stream",
                "content_encoding": None,
                "content_disposition": "attachment",
                "cache_control": None,
            }
        },
    )
    upload_mock = AsyncMock()
    multipart_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
        patch("app.services.backup.upload_file_multipart", multipart_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    multipart_mock.assert_called_once()
    upload_mock.assert_not_called()


@pytest.mark.asyncio
async def test_small_object_uses_single_put(db_session: AsyncSession, tmp_path: Path) -> None:
    """Objects < RESTORE_MULTIPART_THRESHOLD must use upload_file (single PUT)."""
    small_data = b"y" * (_RESTORE_MULTIPART_THRESHOLD - 1)
    zip_path = _make_zip(
        tmp_path,
        s3_entries={"cas/small": small_data},
        s3_metadata={
            "cas/small": {
                "content_type": "application/octet-stream",
                "content_encoding": None,
                "content_disposition": "attachment",
                "cache_control": None,
            }
        },
    )
    upload_mock = AsyncMock()
    multipart_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
        patch("app.services.backup.upload_file_multipart", multipart_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    upload_mock.assert_called_once()
    multipart_mock.assert_not_called()


@pytest.mark.asyncio
async def test_multipart_receives_correct_metadata(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """upload_file_multipart must receive the same metadata headers as upload_file."""
    large_data = b"z" * _RESTORE_MULTIPART_THRESHOLD
    zip_path = _make_zip(
        tmp_path,
        s3_entries={"cas/lg": large_data},
        s3_metadata={
            "cas/lg": {
                "content_type": "video/mp4",
                "content_encoding": "gzip",
                "content_disposition": "attachment",
                "cache_control": None,
            }
        },
    )
    multipart_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
        patch("app.services.backup.upload_file_multipart", multipart_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    kw = multipart_mock.call_args[1]
    assert kw["content_type"] == "video/mp4"
    assert kw["content_encoding"] == "gzip"


# ── Branding prefix ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_branding_objects_wiped_on_restore(db_session: AsyncSession, tmp_path: Path) -> None:
    """Existing branding/ objects must be deleted before restore."""
    zip_path = _make_zip(tmp_path, s3_metadata={})
    delete_mock = AsyncMock()
    copy_mock = AsyncMock()

    async def _fake_list(prefix: str):
        if prefix == "branding/":
            yield {"Key": "branding/old_logo.png"}

    with (
        patch("app.services.backup.list_objects", side_effect=_fake_list),
        patch("app.services.backup.copy_object", copy_mock),
        patch("app.services.backup.delete_object", delete_mock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    delete_mock.assert_any_call("branding/old_logo.png")
    assert copy_mock.await_args_list[0].args[0] == "branding/old_logo.png"


@pytest.mark.asyncio
async def test_branding_object_reuploaded_on_restore(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """branding/ entry in the ZIP must be re-uploaded during restore."""
    zip_path = _make_zip(
        tmp_path,
        s3_entries={"branding/logo.svg": b"<svg/>"},
        s3_metadata={
            "branding/logo.svg": {
                "content_type": "image/svg+xml",
                "content_encoding": None,
                "content_disposition": None,
                "cache_control": None,
            }
        },
    )
    upload_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    keys_uploaded = [c.args[1] for c in upload_mock.call_args_list]
    assert "branding/logo.svg" in keys_uploaded


# ── Fault tolerance ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_truncate_failure_aborts_restore(db_session: AsyncSession, tmp_path: Path) -> None:
    """A partial database wipe must abort before object storage is touched."""
    zip_path = _make_zip(tmp_path, s3_metadata={})
    original_execute = db_session.execute

    call_count = {"n": 0}

    async def _patched_execute(stmt, *args, **kwargs):
        sql_str = str(stmt)
        if "DELETE" in sql_str and "dead_letter_jobs" in sql_str and call_count["n"] == 0:
            call_count["n"] += 1
            raise Exception("table missing on old instance")
        return await original_execute(stmt, *args, **kwargs)

    db_session.execute = _patched_execute  # type: ignore[method-assign]
    try:
        with (
            patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
            patch("app.services.backup.delete_object", new_callable=AsyncMock),
            patch("app.services.backup.upload_file", new_callable=AsyncMock),
        ):
            with pytest.raises(Exception, match="table missing"):
                await restore_from_zip_path(db_session, zip_path)
    finally:
        db_session.execute = original_execute  # type: ignore[method-assign]


# ── Byte integrity ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gzip_bytes_restored_without_decompression(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Raw gzip bytes in the ZIP must be sent as-is to upload_file, not decompressed."""
    import gzip
    import io

    original = b"secret content"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(original)
    gz_bytes = buf.getvalue()

    zip_path = _make_zip(
        tmp_path,
        s3_entries={"cas/gz_obj": gz_bytes},
        s3_metadata={
            "cas/gz_obj": {
                "content_type": "application/octet-stream",
                "content_encoding": "gzip",
                "content_disposition": "attachment",
                "cache_control": None,
            }
        },
    )
    upload_mock = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", upload_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    uploaded_data = upload_mock.call_args.args[0]
    # Must be the raw gzip bytes, NOT the decompressed payload
    assert uploaded_data == gz_bytes
    assert uploaded_data != original
