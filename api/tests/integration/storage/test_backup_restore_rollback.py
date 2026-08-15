from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.storage import facade
from app.services.backup import _TABLE_INSERT_ORDER, BACKUP_VERSION, restore_from_zip_path

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_failed_restore_recovers_overwritten_seaweedfs_object(tmp_path: Path) -> None:
    existing_key = "cas/backup-rollback-existing"
    failing_key = "cas/backup-rollback-failing"
    await facade.upload_file(b"original", existing_key)

    backup = tmp_path / "restore.zip"
    replacement = b"replacement"
    failing_payload = b"must fail"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": BACKUP_VERSION,
                    "s3_object_count": 2,
                    "s3_objects": {
                        existing_key: {
                            "size": len(replacement),
                            "sha256": hashlib.sha256(replacement).hexdigest(),
                        },
                        failing_key: {
                            "size": len(failing_payload),
                            "sha256": hashlib.sha256(failing_payload).hexdigest(),
                        },
                    },
                }
            ),
        )
        archive.writestr("s3_metadata.json", "{}")
        for table in _TABLE_INSERT_ORDER:
            archive.writestr(f"db/{table}.json", "[]")
        archive.writestr(f"s3/{existing_key}", replacement)
        archive.writestr(f"s3/{failing_key}", failing_payload)

    real_upload = facade.upload_file

    async def fail_second_upload(data: bytes, key: str, **kwargs: object) -> None:
        if key == failing_key:
            raise OSError("injected storage failure")
        await real_upload(data, key, **kwargs)  # type: ignore[arg-type]

    with patch("app.services.backup.upload_file", side_effect=fail_second_upload):
        with pytest.raises(OSError, match="injected storage failure"):
            await restore_from_zip_path(AsyncMock(), backup)

    assert await facade.read_full_object(existing_key) == b"original"
    assert not await facade.object_exists(failing_key)
    rollback_objects = [obj async for obj in facade.list_objects("restore-rollback/")]
    assert rollback_objects == []
