"""Comprehensive tests for the admin backup/restore feature."""

from __future__ import annotations

import json
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import NotFoundError
from app.core.security.security import create_access_token
from app.models.directory import Directory, DirectoryType
from app.models.material import Material
from app.models.pull_request import PRStatus, PullRequest
from app.models.tag import Tag
from app.models.user import User, UserRole
from app.services.backup import (
    _TABLE_INSERT_ORDER,
    BACKUP_VERSION,
    MAX_LOCAL_BACKUPS,
    _deserialize_row,
    _deserialize_value,
    _serialize_row,
    _topological_sort,
    backup_filename,
    create_backup_zip,
    enforce_backup_rotation,
    list_local_backups,
    restore_from_zip_path,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def test_production_requires_offline_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.routers.admin_backup import _require_offline_restore_in_production

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(HTTPException) as raised:
        _require_offline_restore_in_production()

    assert raised.value.status_code == 503
    assert "restore-backup-offline" in str(raised.value.detail)


async def _make_admin(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"admin_{uuid.uuid4().hex[:6]}@test.com",
        display_name="Admin",
        role=UserRole.BUREAU,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_student(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"student_{uuid.uuid4().hex[:6]}@test.com",
        display_name="Student",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


def _auth(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def _make_directory(
    db: AsyncSession, user: User, parent: Directory | None = None
) -> Directory:
    d = Directory(
        id=uuid.uuid4(),
        name="Test Dir",
        slug=f"test-dir-{uuid.uuid4().hex[:6]}",
        type=DirectoryType.FOLDER,
        parent_id=parent.id if parent else None,
        created_by=user.id,
    )
    db.add(d)
    await db.flush()
    return d


async def _make_material(
    db: AsyncSession, user: User, directory: Directory | None = None
) -> Material:
    m = Material(
        id=uuid.uuid4(),
        title="Test Material",
        slug=f"test-mat-{uuid.uuid4().hex[:6]}",
        type="document",
        directory_id=directory.id if directory else None,
        author_id=user.id,
    )
    db.add(m)
    await db.flush()
    return m


async def _make_pull_request(db: AsyncSession, user: User) -> PullRequest:
    pr = PullRequest(
        id=uuid.uuid4(),
        title="Test PR",
        payload=[],
        author_id=user.id,
        status=PRStatus.OPEN,
    )
    db.add(pr)
    await db.flush()
    return pr


def _make_mock_s3() -> tuple[AsyncMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    """Return (mock_list_objects, mock_download_raw, mock_get_headers, mock_delete, mock_upload) mocks."""

    async def _empty_gen(*args, **kwargs):
        if False:
            yield  # make it an async generator

    list_mock = MagicMock(return_value=_empty_gen())
    download_raw_mock = AsyncMock()
    get_headers_mock = AsyncMock(return_value={})
    delete_mock = AsyncMock()
    upload_mock = AsyncMock()
    return list_mock, download_raw_mock, get_headers_mock, delete_mock, upload_mock


# ── Unit tests: serialization ─────────────────────────────────────────────────


def test_serialize_uuid() -> None:
    uid = uuid.uuid4()
    assert _serialize_row({"id": uid})["id"] == str(uid)


def test_serialize_datetime() -> None:
    dt = datetime(2026, 4, 25, 10, 30, tzinfo=UTC)
    serialized = _serialize_row({"created_at": dt})["created_at"]
    assert "2026-04-25" in serialized


def test_serialize_none() -> None:
    assert _serialize_row({"x": None})["x"] is None


def test_deserialize_uuid_string() -> None:
    uid = uuid.uuid4()
    result = _deserialize_value(str(uid))
    # UUIDs are kept as strings for cross-DB compatibility (SQLite can't bind uuid.UUID)
    assert isinstance(result, str)
    assert result == str(uid)


def test_deserialize_iso_datetime() -> None:
    dt = datetime(2026, 4, 25, 10, 30, tzinfo=UTC)
    result = _deserialize_value(dt.isoformat())
    assert isinstance(result, datetime)


def test_deserialize_plain_string() -> None:
    assert _deserialize_value("hello world") == "hello world"


def test_deserialize_none() -> None:
    assert _deserialize_row({"x": None})["x"] is None


def test_deserialize_non_string_passthrough() -> None:
    assert _deserialize_value(42) == 42
    assert _deserialize_value({"a": 1}) == {"a": 1}


# ── Unit tests: topological sort ──────────────────────────────────────────────


def test_topological_sort_root_first() -> None:
    root_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    rows = [
        {"id": child_id, "parent_id": root_id},
        {"id": root_id, "parent_id": None},
    ]
    sorted_rows = _topological_sort(rows, pk_col="id", fk_col="parent_id")
    ids = [r["id"] for r in sorted_rows]
    assert ids.index(root_id) < ids.index(child_id)


def test_topological_sort_deep_chain() -> None:
    ids = [str(uuid.uuid4()) for _ in range(5)]
    # 0 → 1 → 2 → 3 → 4 (child → parent direction)
    rows = [
        {"id": ids[4], "parent_id": None},
        {"id": ids[3], "parent_id": ids[4]},
        {"id": ids[2], "parent_id": ids[3]},
        {"id": ids[1], "parent_id": ids[2]},
        {"id": ids[0], "parent_id": ids[1]},
    ]
    import random

    random.shuffle(rows)
    sorted_rows = _topological_sort(rows, pk_col="id", fk_col="parent_id")
    positions = {r["id"]: i for i, r in enumerate(sorted_rows)}
    for child, parent in [(ids[0], ids[1]), (ids[1], ids[2]), (ids[2], ids[3]), (ids[3], ids[4])]:
        assert positions[parent] < positions[child]


def test_topological_sort_no_parent() -> None:
    rows = [{"id": str(uuid.uuid4()), "parent_id": None} for _ in range(5)]
    result = _topological_sort(rows, pk_col="id", fk_col="parent_id")
    assert len(result) == 5


def test_topological_sort_external_parent_ignored() -> None:
    """Parent that doesn't exist in rows is ignored (not in backup scope)."""
    orphan_parent = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    rows = [{"id": child_id, "parent_id": orphan_parent}]
    result = _topological_sort(rows, pk_col="id", fk_col="parent_id")
    assert len(result) == 1
    assert result[0]["id"] == child_id


# ── Unit tests: backup_filename ───────────────────────────────────────────────


def test_backup_filename_format() -> None:
    name = backup_filename()
    assert name.startswith("backup_")
    timestamp, random_suffix = name.removeprefix("backup_").rsplit("_", 1)
    assert len(timestamp) == len("20260425_103045")
    assert len(random_suffix) == 32
    assert all(character in "0123456789abcdef" for character in random_suffix)


def test_backup_filename_unique() -> None:
    names = {backup_filename() for _ in range(3)}
    assert len(names) == 3
    for name in names:
        assert name.startswith("backup_")


def test_backup_resolution_rejects_an_existing_absolute_path(tmp_path: Path) -> None:
    from app.routers.admin_backup import _resolve_backup

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    outside = tmp_path / "backup_20260425_103045.zip"
    outside.write_bytes(b"sensitive")

    with pytest.raises(NotFoundError):
        _resolve_backup(str(outside.with_suffix("")), backup_dir)


# ── Unit tests: local backup management ──────────────────────────────────────


def test_list_local_backups_empty(tmp_path: Path) -> None:
    assert list_local_backups(tmp_path) == []


def test_list_local_backups_returns_sorted(tmp_path: Path) -> None:
    names = [
        "backup_20260101_000000.zip",
        "backup_20260103_000000.zip",
        "backup_20260102_000000.zip",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    result = list_local_backups(tmp_path)
    filenames = [r["filename"] for r in result]
    assert filenames == sorted(names)


def test_list_local_backups_metadata_fields(tmp_path: Path) -> None:
    (tmp_path / "backup_20260101_000000.zip").write_bytes(b"data")
    result = list_local_backups(tmp_path)
    assert len(result) == 1
    entry = result[0]
    assert entry["id"] == "backup_20260101_000000"
    assert entry["filename"] == "backup_20260101_000000.zip"
    assert "created_at" in entry
    assert entry["size_bytes"] == 4


def test_enforce_rotation_no_op_under_limit(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"backup_2026010{i}_000000.zip").write_bytes(b"x")
    deleted = enforce_backup_rotation(tmp_path, max_count=3)
    assert deleted == []
    assert len(list(tmp_path.glob("*.zip"))) == 3


def test_enforce_rotation_deletes_oldest(tmp_path: Path) -> None:
    for i in range(4):
        (tmp_path / f"backup_2026010{i}_000000.zip").write_bytes(b"x")
    deleted = enforce_backup_rotation(tmp_path, max_count=3)
    assert len(deleted) == 1
    assert "backup_20260100_000000.zip" in deleted
    assert not (tmp_path / "backup_20260100_000000.zip").exists()


def test_enforce_rotation_respects_max_local_backups(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"backup_202601{i:02d}_000000.zip").write_bytes(b"x")
    enforce_backup_rotation(tmp_path, max_count=MAX_LOCAL_BACKUPS)
    remaining = list(tmp_path.glob("*.zip"))
    assert len(remaining) == MAX_LOCAL_BACKUPS


# ── Unit tests: create_backup_zip ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_backup_zip_structure(db_session: AsyncSession, tmp_path: Path) -> None:
    """The ZIP must contain manifest.json and db/*.json for all tables."""
    dest = tmp_path / "backup.zip"

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        manifest = await create_backup_zip(db_session, dest)

    assert dest.exists()
    with zipfile.ZipFile(dest, "r") as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        for tbl in _TABLE_INSERT_ORDER:
            assert f"db/{tbl}.json" in names

    assert manifest["version"] == BACKUP_VERSION
    assert "created_at" in manifest
    assert manifest["s3_object_count"] == 0


@pytest.mark.asyncio
async def test_create_backup_zip_includes_db_rows(db_session: AsyncSession, tmp_path: Path) -> None:
    """Rows present in DB at backup time must appear in the ZIP."""
    user = await _make_admin(db_session)
    tag = Tag(id=uuid.uuid4(), name=f"tag-{uuid.uuid4().hex[:6]}")
    db_session.add(tag)
    await db_session.flush()

    dest = tmp_path / "backup.zip"

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        manifest = await create_backup_zip(db_session, dest)

    with zipfile.ZipFile(dest, "r") as zf:
        users_data = json.loads(zf.read("db/users.json"))
        tags_data = json.loads(zf.read("db/tags.json"))

    user_ids = [r["id"] for r in users_data]
    assert str(user.id) in user_ids
    tag_ids = [r["id"] for r in tags_data]
    assert str(tag.id) in tag_ids
    assert manifest["db_row_counts"]["users"] >= 1
    assert manifest["db_row_counts"]["tags"] >= 1


@pytest.mark.asyncio
async def test_create_backup_zip_includes_s3_objects(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """S3 objects listed under backup prefixes must appear in the ZIP."""
    dest = tmp_path / "backup.zip"
    s3_file_content = b"binary content"
    s3_tmp_file = tmp_path / "fake_s3_object"
    s3_tmp_file.write_bytes(s3_file_content)

    call_count: dict[str, int] = {"n": 0}

    async def _fake_list(prefix: str):
        if prefix == "cas/" and call_count["n"] == 0:
            call_count["n"] += 1
            yield {"Key": "cas/abc123", "Size": len(s3_file_content)}
        else:
            if False:
                yield

    async def _fake_download_raw(key: str, dest_path: str | Path) -> None:
        Path(dest_path).write_bytes(s3_file_content)

    with (
        patch("app.services.backup.list_objects", side_effect=_fake_list),
        patch("app.services.backup.download_file_raw", side_effect=_fake_download_raw),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        manifest = await create_backup_zip(db_session, dest)

    assert manifest["s3_object_count"] == 1

    with zipfile.ZipFile(dest, "r") as zf:
        assert "s3/cas/abc123" in zf.namelist()
        assert zf.read("s3/cas/abc123") == s3_file_content


# ── Unit tests: restore_from_zip_path ────────────────────────────────────────


def _make_minimal_zip(tmp_path: Path, rows: dict[str, list[dict]] | None = None) -> Path:
    """Build a minimal valid backup ZIP for restore tests."""
    dest = tmp_path / "test_backup.zip"
    db_data = rows or {}
    manifest = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "tables": _TABLE_INSERT_ORDER,
        "s3_prefixes": ["cas/", "uploads/", "thumbnails/"],
        "s3_object_count": 0,
        "db_row_counts": {t: len(db_data.get(t, [])) for t in _TABLE_INSERT_ORDER},
    }
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for tbl in _TABLE_INSERT_ORDER:
            zf.writestr(f"db/{tbl}.json", json.dumps(db_data.get(tbl, [])))
    return dest


@pytest.mark.asyncio
async def test_restore_clears_existing_data(db_session: AsyncSession, tmp_path: Path) -> None:
    """After restore, rows not in the backup must be gone."""
    # Pre-existing tag that is NOT in the backup
    pre_tag = Tag(id=uuid.uuid4(), name=f"pre-existing-{uuid.uuid4().hex[:6]}")
    db_session.add(pre_tag)
    await db_session.flush()

    zip_path = _make_minimal_zip(tmp_path)

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    from sqlalchemy import text as sa_text

    result = await db_session.execute(sa_text("SELECT COUNT(*) FROM tags"))
    count = result.scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_restore_inserts_backup_rows(db_session: AsyncSession, tmp_path: Path) -> None:
    """Rows in the backup ZIP must be present in the DB after restore."""
    user_id = uuid.uuid4()
    tag_id = uuid.uuid4()
    rows: dict[str, list[dict]] = {
        "users": [
            {
                "id": str(user_id),
                "email": "restored@test.com",
                "display_name": "Restored",
                "role": "bureau",
                "onboarded": True,
                "gdpr_consent": True,
                "gdpr_consent_at": None,
                "avatar_url": None,
                "bio": None,
                "academic_year": None,
                "password_hash": None,
                "is_flagged": False,
                "auto_approve": False,
                "created_at": datetime.now(UTC).isoformat(),
                "deleted_at": None,
                "last_login_at": None,
            }
        ],
        "tags": [
            {
                "id": str(tag_id),
                "name": "restored-tag",
                "category": None,
            }
        ],
    }
    zip_path = _make_minimal_zip(tmp_path, rows=rows)

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    from sqlalchemy import text as sa_text

    result = await db_session.execute(
        sa_text("SELECT email FROM users WHERE id = :id"), {"id": str(user_id)}
    )
    row = result.first()
    assert row is not None
    assert row[0] == "restored@test.com"

    result = await db_session.execute(
        sa_text("SELECT name FROM tags WHERE id = :id"), {"id": str(tag_id)}
    )
    tag_row = result.first()
    assert tag_row is not None
    assert tag_row[0] == "restored-tag"


@pytest.mark.asyncio
async def test_restore_directories_topological_order(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Directories with parent_id must be restored so parents exist before children."""
    root_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    user_row: dict[str, Any] = {
        "id": user_id,
        "email": "u@test.com",
        "display_name": "U",
        "role": "bureau",
        "onboarded": True,
        "gdpr_consent": True,
        "gdpr_consent_at": None,
        "avatar_url": None,
        "bio": None,
        "academic_year": None,
        "password_hash": None,
        "is_flagged": False,
        "auto_approve": False,
        "created_at": now,
        "deleted_at": None,
        "last_login_at": None,
    }
    dir_row_root: dict[str, Any] = {
        "id": root_id,
        "parent_id": None,
        "name": "Root",
        "slug": "root",
        "type": "folder",
        "description": None,
        "metadata": {},
        "sort_order": 0,
        "like_count": 0,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    dir_row_child: dict[str, Any] = {
        "id": child_id,
        "parent_id": root_id,
        "name": "Child",
        "slug": "child",
        "type": "folder",
        "description": None,
        "metadata": {},
        "sort_order": 0,
        "like_count": 0,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    # Deliberately put child before root to test topological sort
    rows: dict[str, list[dict]] = {
        "users": [user_row],
        "directories": [dir_row_child, dir_row_root],
    }
    zip_path = _make_minimal_zip(tmp_path, rows=rows)

    async def _empty_gen(*a, **kw):
        return
        yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    from sqlalchemy import text as sa_text

    result = await db_session.execute(sa_text("SELECT COUNT(*) FROM directories"))
    assert result.scalar_one() == 2


@pytest.mark.asyncio
async def test_restore_pr_deferred_self_refs(db_session: AsyncSession, tmp_path: Path) -> None:
    """PR reverts_pr_id/reverted_by_pr_id cross-refs must be restored after insert."""
    now = datetime.now(UTC).isoformat()
    user_id = str(uuid.uuid4())
    pr1_id = str(uuid.uuid4())
    pr2_id = str(uuid.uuid4())

    user_row: dict[str, Any] = {
        "id": user_id,
        "email": "u@test.com",
        "display_name": "U",
        "role": "bureau",
        "onboarded": True,
        "gdpr_consent": True,
        "gdpr_consent_at": None,
        "avatar_url": None,
        "bio": None,
        "academic_year": None,
        "password_hash": None,
        "is_flagged": False,
        "auto_approve": False,
        "created_at": now,
        "deleted_at": None,
        "last_login_at": None,
    }

    def _pr_row(pr_id: str, revert_id: str | None = None) -> dict[str, Any]:
        return {
            "id": pr_id,
            "type": "batch",
            "status": "approved",
            "title": "PR",
            "description": None,
            "payload": [],
            "applied_result": None,
            "summary_types": [],
            "author_id": user_id,
            "reviewed_by": None,
            "virus_scan_result": "clean",
            "rejection_reason": None,
            "approved_at": now,
            "reverts_pr_id": revert_id,
            "reverted_by_pr_id": pr2_id if pr_id == pr1_id else None,
            "created_at": now,
            "updated_at": now,
        }

    rows: dict[str, list[dict]] = {
        "users": [user_row],
        "pull_requests": [_pr_row(pr2_id), _pr_row(pr1_id, revert_id=pr2_id)],
    }
    zip_path = _make_minimal_zip(tmp_path, rows=rows)

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    from sqlalchemy import text as sa_text

    result = await db_session.execute(
        sa_text("SELECT reverts_pr_id FROM pull_requests WHERE id = :id"),
        {"id": pr1_id},
    )
    row = result.first()
    assert row is not None
    assert str(row[0]) == pr2_id


@pytest.mark.asyncio
async def test_restore_invalid_version(db_session: AsyncSession, tmp_path: Path) -> None:
    dest = tmp_path / "bad.zip"
    manifest = {"version": "99.0", "created_at": datetime.now(UTC).isoformat()}
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for tbl in _TABLE_INSERT_ORDER:
            zf.writestr(f"db/{tbl}.json", "[]")

    with pytest.raises(ValueError, match="Incompatible backup version"):
        await restore_from_zip_path(db_session, dest)


@pytest.mark.asyncio
async def test_restore_wipes_s3_and_reuploads(db_session: AsyncSession, tmp_path: Path) -> None:
    """Existing S3 objects must be deleted and backup S3 objects re-uploaded."""
    zip_path = tmp_path / "backup.zip"
    s3_content = b"hello s3"
    with zipfile.ZipFile(zip_path, "w") as zf:
        manifest = {
            "version": BACKUP_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "tables": _TABLE_INSERT_ORDER,
            "s3_prefixes": ["cas/", "uploads/", "thumbnails/"],
            "s3_object_count": 1,
            "db_row_counts": {},
        }
        zf.writestr("manifest.json", json.dumps(manifest))
        for tbl in _TABLE_INSERT_ORDER:
            zf.writestr(f"db/{tbl}.json", "[]")
        zf.writestr("s3/cas/deadbeef", s3_content)

    delete_mock = AsyncMock()
    upload_mock = AsyncMock()
    copy_mock = AsyncMock()

    existing_s3 = [{"Key": "cas/old_file", "Size": 5}]

    async def _fake_list(prefix: str):
        for obj in existing_s3:
            if obj["Key"].startswith(prefix):
                yield obj

    with (
        patch("app.services.backup.list_objects", side_effect=_fake_list),
        patch("app.services.backup.copy_object", copy_mock),
        patch("app.services.backup.delete_object", delete_mock),
        patch("app.services.backup.upload_file", upload_mock),
    ):
        await restore_from_zip_path(db_session, zip_path)

    delete_mock.assert_any_call("cas/old_file")
    assert copy_mock.await_args_list[0].args[0] == "cas/old_file"
    assert copy_mock.await_args_list[0].args[1].startswith("restore-rollback/")
    upload_mock.assert_called_once()
    call_args = upload_mock.call_args
    assert call_args[0][0] == s3_content
    assert call_args[0][1] == "cas/deadbeef"


@pytest.mark.asyncio
async def test_restore_returns_manifest(db_session: AsyncSession, tmp_path: Path) -> None:
    zip_path = _make_minimal_zip(tmp_path)

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        result = await restore_from_zip_path(db_session, zip_path)

    assert result["version"] == BACKUP_VERSION
    assert "created_at" in result


# ── API endpoint tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_backups_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()
    with patch("app.routers.admin_backup.settings") as mock_settings:
        with tempfile.TemporaryDirectory() as tmp:
            mock_settings.backup_dir = tmp
            r = await client.get("/api/admin/backup", headers=_auth(admin))
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_backups_requires_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _make_student(db_session)
    await db_session.commit()
    r = await client.get("/api/admin/backup", headers=_auth(student))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_backups_unauthenticated(client: AsyncClient) -> None:
    r = await client.get("/api/admin/backup")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_save_backup_creates_file(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with tempfile.TemporaryDirectory() as tmp:
        with (
            patch("app.routers.admin_backup.settings") as mock_settings,
            patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
            patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
            patch(
                "app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}
            ),
        ):
            mock_settings.backup_dir = tmp
            r = await client.post("/api/admin/backup/save", headers=_auth(admin))

        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "ok"
        assert "backup" in data
        assert "manifest" in data
        assert data["manifest"]["version"] == BACKUP_VERSION


@pytest.mark.asyncio
async def test_save_backup_enforces_rotation(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Pre-create MAX_LOCAL_BACKUPS files so next save triggers rotation
        for i in range(MAX_LOCAL_BACKUPS):
            (tmp_path / f"backup_2026010{i}_000000.zip").write_bytes(b"old")

        with (
            patch("app.routers.admin_backup.settings") as mock_settings,
            patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
            patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
            patch(
                "app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}
            ),
        ):
            mock_settings.backup_dir = tmp
            r = await client.post("/api/admin/backup/save", headers=_auth(admin))

        assert r.status_code == 201
        # After rotation: MAX_LOCAL_BACKUPS files remain
        remaining = list(tmp_path.glob("*.zip"))
        assert len(remaining) == MAX_LOCAL_BACKUPS


@pytest.mark.asyncio
async def test_save_backup_requires_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _make_student(db_session)
    await db_session.commit()
    r = await client.post("/api/admin/backup/save", headers=_auth(student))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_export_backup_streams_zip(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    async def _empty_gen(*a, **kw):
        if False:
            yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        r = await client.get("/api/admin/backup/export", headers=_auth(admin))

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "backup_" in r.headers.get("content-disposition", "")
    # Verify the response body is a valid ZIP
    content = r.content
    assert zipfile.is_zipfile(BytesIO(content))


@pytest.mark.asyncio
async def test_export_backup_requires_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _make_student(db_session)
    await db_session.commit()
    r = await client.get("/api/admin/backup/export", headers=_auth(student))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_download_local_backup(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    with tempfile.TemporaryDirectory() as tmp:
        backup_path = Path(tmp) / "backup_20260425_103000.zip"
        backup_path.write_bytes(b"PK\x03\x04fake zip content")

        with patch("app.routers.admin_backup.settings") as mock_settings:
            mock_settings.backup_dir = tmp
            r = await client.get(
                "/api/admin/backup/backup_20260425_103000/download",
                headers=_auth(admin),
            )

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


@pytest.mark.asyncio
async def test_download_nonexistent_backup(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    with tempfile.TemporaryDirectory() as tmp:
        with patch("app.routers.admin_backup.settings") as mock_settings:
            mock_settings.backup_dir = tmp
            r = await client.get(
                "/api/admin/backup/backup_nonexistent/download",
                headers=_auth(admin),
            )

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_local_backup(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    with tempfile.TemporaryDirectory() as tmp:
        backup_path = Path(tmp) / "backup_20260425_103000.zip"
        backup_path.write_bytes(b"content")

        with patch("app.routers.admin_backup.settings") as mock_settings:
            mock_settings.backup_dir = tmp
            r = await client.delete(
                "/api/admin/backup/backup_20260425_103000",
                headers=_auth(admin),
            )

    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert not backup_path.exists()


@pytest.mark.asyncio
async def test_delete_nonexistent_backup(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    with tempfile.TemporaryDirectory() as tmp:
        with patch("app.routers.admin_backup.settings") as mock_settings:
            mock_settings.backup_dir = tmp
            r = await client.delete(
                "/api/admin/backup/backup_nonexistent",
                headers=_auth(admin),
            )

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_backup_requires_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _make_student(db_session)
    await db_session.commit()
    r = await client.delete("/api/admin/backup/some_id", headers=_auth(student))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_restore_local_backup(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    async def _empty_gen(*a, **kw):
        return
        yield

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "backup_20260425_103000.zip"
        manifest = {
            "version": BACKUP_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "tables": _TABLE_INSERT_ORDER,
            "s3_prefixes": ["cas/", "uploads/", "thumbnails/"],
            "s3_object_count": 0,
            "db_row_counts": {},
        }
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for tbl in _TABLE_INSERT_ORDER:
                zf.writestr(f"db/{tbl}.json", "[]")

        with (
            patch("app.routers.admin_backup.settings") as mock_settings,
            patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
            patch("app.services.backup.delete_object", new_callable=AsyncMock),
            patch("app.services.backup.upload_file", new_callable=AsyncMock),
        ):
            mock_settings.backup_dir = tmp
            r = await client.post(
                "/api/admin/backup/backup_20260425_103000/restore",
                headers=_auth(admin),
            )

    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["manifest"]["version"] == BACKUP_VERSION


@pytest.mark.asyncio
async def test_restore_local_backup_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    with tempfile.TemporaryDirectory() as tmp:
        with patch("app.routers.admin_backup.settings") as mock_settings:
            mock_settings.backup_dir = tmp
            r = await client.post(
                "/api/admin/backup/backup_nonexistent/restore",
                headers=_auth(admin),
            )

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_restore_local_backup_requires_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    student = await _make_student(db_session)
    await db_session.commit()
    r = await client.post("/api/admin/backup/some_id/restore", headers=_auth(student))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_restore_upload(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    manifest = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "tables": _TABLE_INSERT_ORDER,
        "s3_prefixes": ["cas/", "uploads/", "thumbnails/"],
        "s3_object_count": 0,
        "db_row_counts": {},
    }
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for tbl in _TABLE_INSERT_ORDER:
            zf.writestr(f"db/{tbl}.json", "[]")
    zip_bytes = buf.getvalue()

    async def _empty_gen(*a, **kw):
        return
        yield

    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        r = await client.post(
            "/api/admin/backup/restore/upload",
            headers=_auth(admin),
            files={"file": ("backup_20260425_103000.zip", BytesIO(zip_bytes), "application/zip")},
        )

    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_restore_upload_rejects_non_zip(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()
    r = await client.post(
        "/api/admin/backup/restore/upload",
        headers=_auth(admin),
        files={"file": ("data.tar.gz", BytesIO(b"not a zip"), "application/gzip")},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_restore_upload_rejects_incompatible_version(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"version": "99.0"}))
        for tbl in _TABLE_INSERT_ORDER:
            zf.writestr(f"db/{tbl}.json", "[]")
    zip_bytes = buf.getvalue()

    r = await client.post(
        "/api/admin/backup/restore/upload",
        headers=_auth(admin),
        files={"file": ("backup.zip", BytesIO(zip_bytes), "application/zip")},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_restore_upload_requires_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _make_student(db_session)
    await db_session.commit()
    r = await client.post(
        "/api/admin/backup/restore/upload",
        headers=_auth(student),
        files={"file": ("backup.zip", BytesIO(b""), "application/zip")},
    )
    assert r.status_code == 403


# ── Path traversal security test ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_path_traversal_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _make_admin(db_session)
    await db_session.commit()

    with tempfile.TemporaryDirectory() as tmp:
        with patch("app.routers.admin_backup.settings") as mock_settings:
            mock_settings.backup_dir = tmp
            r = await client.get(
                "/api/admin/backup/../../../etc/passwd/download",
                headers=_auth(admin),
            )

    assert r.status_code in (404, 422)


# ── Round-trip integration test ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_roundtrip_backup_and_restore(db_session: AsyncSession, tmp_path: Path) -> None:
    """Full round-trip: create data → backup → wipe → restore → verify data."""
    user = await _make_admin(db_session)
    tag = Tag(id=uuid.uuid4(), name=f"roundtrip-tag-{uuid.uuid4().hex[:6]}")
    db_session.add(tag)
    directory = await _make_directory(db_session, user)
    material = await _make_material(db_session, user, directory)
    await db_session.flush()

    original_user_id = str(user.id)
    original_tag_id = str(tag.id)
    original_dir_id = str(directory.id)
    original_mat_id = str(material.id)

    dest = tmp_path / "roundtrip.zip"

    async def _empty_gen(*a, **kw):
        return
        yield

    # Step 1: Create backup
    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        await create_backup_zip(db_session, dest)

    # Step 2: Restore (full replacement)
    with (
        patch("app.services.backup.list_objects", side_effect=lambda prefix: _empty_gen()),
        patch("app.services.backup.delete_object", new_callable=AsyncMock),
        patch("app.services.backup.upload_file", new_callable=AsyncMock),
    ):
        restored_manifest = await restore_from_zip_path(db_session, dest)

    assert restored_manifest["version"] == BACKUP_VERSION

    # Step 3: Verify data is back
    from sqlalchemy import text as sa_text

    result = await db_session.execute(
        sa_text("SELECT id FROM users WHERE id = :id"), {"id": original_user_id}
    )
    assert result.first() is not None

    result = await db_session.execute(
        sa_text("SELECT id FROM tags WHERE id = :id"), {"id": original_tag_id}
    )
    assert result.first() is not None

    result = await db_session.execute(
        sa_text("SELECT id FROM directories WHERE id = :id"), {"id": original_dir_id}
    )
    assert result.first() is not None

    result = await db_session.execute(
        sa_text("SELECT id FROM materials WHERE id = :id"), {"id": original_mat_id}
    )
    assert result.first() is not None
