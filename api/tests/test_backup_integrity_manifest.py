import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.services.backup import (
    _TABLE_INSERT_ORDER,
    BACKUP_VERSION,
    _validate_backup_archive,
)


def _make_zip(path: Path, manifest: dict, s3_files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s3_metadata.json", "{}")
        for table in _TABLE_INSERT_ORDER:
            zf.writestr(f"db/{table}.json", "[]")
        for key, payload in s3_files.items():
            zf.writestr(f"s3/{key}", payload)


def test_v2_backup_rejects_object_missing_from_integrity_manifest(tmp_path: Path) -> None:
    backup_path = tmp_path / "backup.zip"
    payload = b"test payload"
    manifest = {
        "version": BACKUP_VERSION,
        "s3_object_count": 1,
        "s3_objects": {},  # missing the key
    }
    _make_zip(backup_path, manifest, {"cas/testobj": payload})

    with pytest.raises(ValueError, match="missing from integrity manifest"):
        _validate_backup_archive(backup_path)


def test_v2_backup_rejects_extra_manifest_object(tmp_path: Path) -> None:
    backup_path = tmp_path / "backup.zip"
    payload = b"test payload"
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "version": BACKUP_VERSION,
        "s3_object_count": 1,
        "s3_objects": {
            "cas/testobj": {"size": len(payload), "sha256": digest},
            "cas/extraobj": {"size": 10, "sha256": "a" * 64},  # extra key not in zip
        },
    }
    _make_zip(backup_path, manifest, {"cas/testobj": payload})

    with pytest.raises(ValueError, match="integrity manifest keyset does not match"):
        _validate_backup_archive(backup_path)


def test_v2_backup_rejects_malformed_integrity_entry(tmp_path: Path) -> None:
    backup_path = tmp_path / "backup.zip"
    payload = b"test payload"
    manifest = {
        "version": BACKUP_VERSION,
        "s3_object_count": 1,
        "s3_objects": {
            "cas/testobj": {
                "size": len(payload),
                # sha256 missing
            }
        },
    }
    _make_zip(backup_path, manifest, {"cas/testobj": payload})

    with pytest.raises(ValueError, match="failed its integrity check"):
        _validate_backup_archive(backup_path)


def test_v2_backup_accepts_exact_integrity_keyset(tmp_path: Path) -> None:
    backup_path = tmp_path / "backup.zip"
    payload = b"test payload"
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "version": BACKUP_VERSION,
        "s3_object_count": 1,
        "s3_objects": {
            "cas/testobj": {"size": len(payload), "sha256": digest},
        },
    }
    _make_zip(backup_path, manifest, {"cas/testobj": payload})

    _validate_backup_archive(backup_path)
