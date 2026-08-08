import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.backup import (
    _TABLE_INSERT_ORDER,
    BACKUP_VERSION,
    _validate_backup_archive,
    create_backup_zip,
    restore_from_zip_path,
)


def _write_backup(
    path: Path, *, payload: bytes = b"stored object", expected_sha256: str | None = None
) -> None:
    digest = expected_sha256 or hashlib.sha256(payload).hexdigest()
    manifest = {
        "version": BACKUP_VERSION,
        "s3_object_count": 1,
        "s3_objects": {"cas/object": {"size": len(payload), "sha256": digest}},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("s3_metadata.json", "{}")
        for table in _TABLE_INSERT_ORDER:
            archive.writestr(f"db/{table}.json", "[]")
        archive.writestr("s3/cas/object", payload)


def test_backup_preflight_rejects_an_object_that_fails_manifest_integrity(tmp_path: Path) -> None:
    backup = tmp_path / "backup.zip"
    _write_backup(backup, expected_sha256="0" * 64)

    with pytest.raises(ValueError, match="failed its integrity check"):
        _validate_backup_archive(backup)


@pytest.mark.asyncio
async def test_invalid_backup_is_rejected_before_destructive_restore_calls(tmp_path: Path) -> None:
    backup = tmp_path / "backup.zip"
    _write_backup(backup)
    with zipfile.ZipFile(backup, "a") as archive:
        archive.writestr("unexpected", b"hostile")

    db = AsyncMock()
    delete = AsyncMock()
    with (
        patch("app.services.backup.delete_object", delete),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        with pytest.raises(ValueError, match="unexpected entries"):
            await restore_from_zip_path(db, backup)

    db.execute.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_backup_uses_collision_free_workspace_paths(tmp_path: Path) -> None:
    destination = tmp_path / "backup.zip"
    keys = ("cas/a__b", "cas/a/b")

    async def list_objects(_prefix: str):
        for key in keys:
            yield {"Key": key}

    async def download(key: str, path: Path) -> None:
        path.write_bytes(key.encode())

    with (
        patch("app.services.backup._dump_table", new_callable=AsyncMock, return_value=[]),
        patch("app.services.backup.list_objects", side_effect=list_objects),
        patch("app.services.backup.download_file_raw", side_effect=download),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        manifest = await create_backup_zip(AsyncMock(), destination)

    assert manifest["s3_object_count"] == 2
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("s3/cas/a__b") == b"cas/a__b"
        assert archive.read("s3/cas/a/b") == b"cas/a/b"


@pytest.mark.asyncio
async def test_backup_fails_closed_when_an_object_cannot_be_captured(tmp_path: Path) -> None:
    destination = tmp_path / "backup.zip"

    async def list_objects(_prefix: str):
        yield {"Key": "cas/missing"}

    with (
        patch("app.services.backup._dump_table", new_callable=AsyncMock, return_value=[]),
        patch("app.services.backup.list_objects", side_effect=list_objects),
        patch(
            "app.services.backup.download_file_raw",
            new_callable=AsyncMock,
            side_effect=OSError("short read"),
        ),
    ):
        with pytest.raises(RuntimeError, match="could not capture object"):
            await create_backup_zip(AsyncMock(), destination)

    assert not destination.exists()


@pytest.mark.asyncio
async def test_restore_upload_failure_restores_the_storage_snapshot(tmp_path: Path) -> None:
    backup = tmp_path / "backup.zip"
    _write_backup(backup)

    async def existing_objects(prefix: str):
        if prefix == "cas/":
            yield {"Key": "cas/object"}

    copy = AsyncMock()
    delete = AsyncMock()
    with (
        patch("app.services.backup.list_objects", side_effect=existing_objects),
        patch("app.services.backup.copy_object", copy),
        patch("app.services.backup.delete_object", delete),
        patch(
            "app.services.backup.upload_file",
            new_callable=AsyncMock,
            side_effect=OSError("storage unavailable"),
        ),
    ):
        with pytest.raises(OSError, match="storage unavailable"):
            await restore_from_zip_path(AsyncMock(), backup)

    snapshot_key = copy.await_args_list[0].args[1]
    assert copy.await_args_list[0].args == ("cas/object", snapshot_key)
    assert copy.await_args_list[1].args == (snapshot_key, "cas/object")
    delete.assert_awaited_once_with(snapshot_key)
