import asyncio
import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.database.database import get_db
from app.services.backup import (
    _TABLE_INSERT_ORDER,
    BACKUP_VERSION,
    _dump_table_to_path,
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


async def _empty_table_dump(_db: object, _table: str, destination: Path) -> tuple[int, int]:
    destination.write_bytes(b"[]")
    return 0, 2


def test_backup_preflight_rejects_an_object_that_fails_manifest_integrity(tmp_path: Path) -> None:
    backup = tmp_path / "backup.zip"
    _write_backup(backup, expected_sha256="0" * 64)

    with pytest.raises(ValueError, match="failed its integrity check"):
        _validate_backup_archive(backup)


@pytest.mark.asyncio
async def test_table_backup_writes_rows_before_consuming_the_next_row(tmp_path: Path) -> None:
    destination = tmp_path / "table.json"

    async def rows():
        yield SimpleNamespace(_mapping={"payload": "a" * (64 * 1024)})
        assert destination.stat().st_size > 0
        yield SimpleNamespace(_mapping={"payload": "second"})

    db = AsyncMock()
    db.stream.return_value = rows()

    count, encoded_bytes = await _dump_table_to_path(db, "uploads", destination)

    assert count == 2
    assert encoded_bytes == destination.stat().st_size
    assert len(json.loads(destination.read_bytes())) == 2


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
        patch(
            "app.services.backup._dump_table_to_path",
            side_effect=_empty_table_dump,
        ),
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
        patch(
            "app.services.backup._dump_table_to_path",
            side_effect=_empty_table_dump,
        ),
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
async def test_backup_starts_only_one_bounded_download_batch(tmp_path: Path) -> None:
    destination = tmp_path / "backup.zip"
    keys = [f"cas/object-{index}" for index in range(25)]
    first_batch_started = asyncio.Event()
    release_downloads = asyncio.Event()
    started: list[str] = []
    active = 0
    max_active = 0

    async def list_objects(prefix: str):
        if prefix == "cas/":
            for key in keys:
                yield {"Key": key}

    async def download(key: str, path: Path) -> None:
        nonlocal active, max_active
        started.append(key)
        active += 1
        max_active = max(max_active, active)
        if len(started) == 10:
            first_batch_started.set()
        await release_downloads.wait()
        path.write_bytes(key.encode())
        active -= 1

    with (
        patch(
            "app.services.backup._dump_table_to_path",
            side_effect=_empty_table_dump,
        ),
        patch("app.services.backup.list_objects", side_effect=list_objects),
        patch("app.services.backup.download_file_raw", side_effect=download),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        backup_task = asyncio.create_task(create_backup_zip(AsyncMock(), destination))
        await asyncio.wait_for(first_batch_started.wait(), timeout=2)
        await asyncio.sleep(0)
        assert len(started) == 10
        release_downloads.set()
        await backup_task

    assert max_active == 10
    assert len(started) == len(keys)


@pytest.mark.asyncio
async def test_backup_rejects_unbounded_object_cardinality_before_download(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "backup.zip"

    async def list_objects(prefix: str):
        if prefix == "cas/":
            yield {"Key": "cas/one"}
            yield {"Key": "cas/two"}

    download = AsyncMock()
    with (
        patch("app.services.backup._BACKUP_MAX_OBJECTS", 1),
        patch(
            "app.services.backup._dump_table_to_path",
            side_effect=_empty_table_dump,
        ),
        patch("app.services.backup.list_objects", side_effect=list_objects),
        patch("app.services.backup.download_file_raw", download),
    ):
        with pytest.raises(RuntimeError, match="object-count limit"):
            await create_backup_zip(AsyncMock(), destination)

    download.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_restore_cancellation_waits_for_storage_compensation(tmp_path: Path) -> None:
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
            side_effect=asyncio.CancelledError,
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await restore_from_zip_path(AsyncMock(), backup)

    snapshot_key = copy.await_args_list[0].args[1]
    assert copy.await_args_list[1].args == (snapshot_key, "cas/object")
    delete.assert_awaited_once_with(snapshot_key)


@pytest.mark.asyncio
async def test_restore_preserves_recovery_journal_when_commit_outcome_is_unknown(
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.zip"
    _write_backup(backup, payload=b"replacement")

    async def existing_objects(prefix: str):
        if prefix == "cas/":
            yield {"Key": "cas/object"}

    db = AsyncMock()
    db.info = {}
    db.commit.side_effect = RuntimeError("commit rejected")
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = db
    session_cm.__aexit__.return_value = None
    copy = AsyncMock()
    delete = AsyncMock()

    with (
        patch("app.core.database.database.async_session_factory", return_value=session_cm),
        patch("app.services.backup.list_objects", side_effect=existing_objects),
        patch("app.services.backup.copy_object", copy),
        patch("app.services.backup.delete_object", delete),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        dependency = get_db()
        session = await dependency.__anext__()
        await restore_from_zip_path(session, backup)
        with pytest.raises(RuntimeError, match="commit rejected"):
            await dependency.__anext__()

    snapshot_key = copy.await_args_list[0].args[1]
    assert len(copy.await_args_list) == 1
    assert copy.await_args_list[0].args == ("cas/object", snapshot_key)
    delete.assert_not_awaited()
