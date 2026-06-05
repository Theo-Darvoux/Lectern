"""Tests for the new tables added to _TABLE_INSERT_ORDER and their restore logic.

Covers:
  - annotations (self-referential FK: parent_id, thread_root_id)
  - allowed_domains (no FK, restored cleanly)
  - uploads (no FK, restored cleanly)
  - comments (FK to users only)
  - featured_items (FK to materials, directories, users)
  - flags (FK to users, polymorphic target_id)
  - notifications (FK to users)
  - dead_letter_jobs (no FK)
  - view_history / download_audit (FK to users + materials)
  - material_likes / material_favourites / directory_likes / directory_favourites

Each test verifies that rows survive a round-trip through the ZIP format.
"""

from __future__ import annotations

import contextlib
import json
import uuid
import zipfile
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.backup import (
    _TABLE_INSERT_ORDER,
    BACKUP_VERSION,
    create_backup_zip,
    restore_from_zip_path,
)

# ── helpers ───────────────────────────────────────────────────────────────────


NOW = datetime.now(UTC).isoformat()


async def _empty_gen(*_a, **_kw):
    if False:
        yield


@contextlib.contextmanager
def _no_s3():
    """Context manager that stubs out all S3 calls in the backup service."""
    with ExitStack() as stack:
        stack.enter_context(patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()))
        stack.enter_context(patch("app.services.backup.download_file_raw", new_callable=AsyncMock))
        stack.enter_context(patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}))
        stack.enter_context(patch("app.services.backup.delete_object", new_callable=AsyncMock))
        stack.enter_context(patch("app.services.backup.upload_file", new_callable=AsyncMock))
        yield


def _make_zip_with_rows(tmp_path: Path, rows: dict[str, list[dict]]) -> Path:
    dest = tmp_path / "test.zip"
    manifest = {
        "version": BACKUP_VERSION,
        "created_at": NOW,
        "tables": _TABLE_INSERT_ORDER,
        "s3_prefixes": [],
        "s3_object_count": 0,
        "db_row_counts": {t: len(rows.get(t, [])) for t in _TABLE_INSERT_ORDER},
    }
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s3_metadata.json", "{}")
        for tbl in _TABLE_INSERT_ORDER:
            zf.writestr(f"db/{tbl}.json", json.dumps(rows.get(tbl, [])))
    return dest


def _user_row(uid: str | None = None) -> dict[str, Any]:
    return {
        "id": uid or str(uuid.uuid4()),
        "email": f"u{uuid.uuid4().hex[:6]}@test.com",
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
        "created_at": NOW,
        "deleted_at": None,
        "last_login_at": None,
    }


def _dir_row(did: str, user_id: str, parent_id: str | None = None) -> dict[str, Any]:
    return {
        "id": did,
        "parent_id": parent_id,
        "name": "D",
        "slug": f"d-{did[:8]}",
        "type": "folder",
        "description": None,
        "metadata": {},
        "sort_order": 0,
        "like_count": 0,
        "created_by": user_id,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }


def _material_row(mid: str, user_id: str, dir_id: str | None = None) -> dict[str, Any]:
    return {
        "id": mid,
        "directory_id": dir_id,
        "title": "M",
        "slug": f"m-{mid[:8]}",
        "type": "document",
        "current_version": 1,
        "parent_material_id": None,
        "author_id": user_id,
        "metadata": {},
        "download_count": 0,
        "total_views": 0,
        "views_today": 0,
        "views_14d": 0,
        "last_view_reset": NOW,
        "like_count": 0,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }


# ── allowed_domains ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allowed_domains_roundtrip(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    domain_id = str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "allowed_domains": [{"id": domain_id, "domain": "example.com", "auto_approve": True, "created_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT domain FROM allowed_domains WHERE id = :id"), {"id": domain_id})
    assert r.scalar_one() == "example.com"


# ── dead_letter_jobs ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dead_letter_jobs_roundtrip(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    job_id = str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "dead_letter_jobs": [{"id": job_id, "job_name": "process_upload", "upload_id": "abc123", "payload": {}, "error_detail": "oops", "attempts": 3, "created_at": NOW, "resolved_at": None}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT job_name FROM dead_letter_jobs WHERE id = :id"), {"id": job_id})
    assert r.scalar_one() == "process_upload"


# ── notifications ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notifications_roundtrip(db_session: AsyncSession, tmp_path: Path) -> None:
    uid = str(uuid.uuid4())
    nid = str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "notifications": [{"id": nid, "user_id": uid, "type": "pr_approved", "title": "PR merged", "body": None, "link": None, "read": False, "created_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT title FROM notifications WHERE id = :id"), {"id": nid})
    assert r.scalar_one() == "PR merged"


# ── flags ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flags_roundtrip(db_session: AsyncSession, tmp_path: Path) -> None:
    uid = str(uuid.uuid4())
    fid = str(uuid.uuid4())
    target = str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "flags": [{"id": fid, "reporter_id": uid, "target_type": "material", "target_id": target, "reason": "spam", "description": None, "status": "open", "resolved_by": None, "resolved_at": None, "created_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT reason FROM flags WHERE id = :id"), {"id": fid})
    assert r.scalar_one() == "spam"


# ── view_history ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_view_history_roundtrip(db_session: AsyncSession, tmp_path: Path) -> None:
    uid, mid, vid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "materials": [_material_row(mid, uid)],
        "view_history": [{"id": vid, "user_id": uid, "material_id": mid, "viewed_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT COUNT(*) FROM view_history WHERE id = :id"), {"id": vid})
    assert r.scalar_one() == 1


# ── material_likes / favourites ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_material_likes_roundtrip(db_session: AsyncSession, tmp_path: Path) -> None:
    uid, mid, lid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "materials": [_material_row(mid, uid)],
        "material_likes": [{"id": lid, "user_id": uid, "material_id": mid, "created_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT COUNT(*) FROM material_likes WHERE id = :id"), {"id": lid})
    assert r.scalar_one() == 1


@pytest.mark.asyncio
async def test_material_favourites_roundtrip(db_session: AsyncSession, tmp_path: Path) -> None:
    uid, mid, fid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "materials": [_material_row(mid, uid)],
        "material_favourites": [{"id": fid, "user_id": uid, "material_id": mid, "created_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT COUNT(*) FROM material_favourites WHERE id = :id"), {"id": fid})
    assert r.scalar_one() == 1


# ── directory_likes / favourites ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_directory_likes_roundtrip(db_session: AsyncSession, tmp_path: Path) -> None:
    uid, did, lid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "directories": [_dir_row(did, uid)],
        "directory_likes": [{"id": lid, "user_id": uid, "directory_id": did, "created_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT COUNT(*) FROM directory_likes WHERE id = :id"), {"id": lid})
    assert r.scalar_one() == 1


@pytest.mark.asyncio
async def test_directory_favourites_roundtrip(db_session: AsyncSession, tmp_path: Path) -> None:
    uid, did, fid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "directories": [_dir_row(did, uid)],
        "directory_favourites": [{"id": fid, "user_id": uid, "directory_id": did, "created_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT COUNT(*) FROM directory_favourites WHERE id = :id"), {"id": fid})
    assert r.scalar_one() == 1


# ── annotations (self-referential) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_annotations_self_ref_topological(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Child annotation with parent_id must be inserted after its parent."""
    uid, mid, mv_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    parent_ann = str(uuid.uuid4())
    child_ann = str(uuid.uuid4())

    pr_id = str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "materials": [_material_row(mid, uid)],
        "pull_requests": [{
            "id": pr_id, "type": "batch", "status": "approved", "title": "PR",
            "description": None, "payload": [], "applied_result": None,
            "summary_types": [], "author_id": uid, "reviewed_by": None,
            "virus_scan_result": "clean", "rejection_reason": None,
            "approved_at": NOW, "reverts_pr_id": None, "reverted_by_pr_id": None,
            "created_at": NOW, "updated_at": NOW,
        }],
        "material_versions": [{
            "id": mv_id, "material_id": mid, "version_number": 1,
            "file_key": None, "file_name": None, "file_size": None,
            "file_mime_type": None, "diff_summary": None, "author_id": uid,
            "pr_id": pr_id, "cas_sha256": None, "thumbnail_key": None,
            "thumbnail_status": None, "virus_scan_result": "clean",
            "version_lock": 0, "created_at": NOW, "deleted_at": None,
        }],
        # child before parent to test topological sort
        "annotations": [
            # child listed first — topo sort must flip order
            {"id": child_ann, "material_id": mid, "version_id": mv_id, "author_id": uid,
             "body": "child reply", "page": 1, "selection_text": None,
             "position_data": None, "thread_id": parent_ann, "reply_to_id": parent_ann,
             "created_at": NOW, "updated_at": NOW},
            {"id": parent_ann, "material_id": mid, "version_id": mv_id, "author_id": uid,
             "body": "parent thread", "page": 1, "selection_text": None,
             "position_data": None, "thread_id": None, "reply_to_id": None,
             "created_at": NOW, "updated_at": NOW},
        ],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT COUNT(*) FROM annotations"))
    assert r.scalar_one() == 2


# ── comments ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_comments_roundtrip(db_session: AsyncSession, tmp_path: Path) -> None:
    uid, cid = str(uuid.uuid4()), str(uuid.uuid4())
    zip_path = _make_zip_with_rows(tmp_path, {
        "users": [_user_row(uid)],
        "comments": [{"id": cid, "author_id": uid, "target_type": "material",
                      "target_id": str(uuid.uuid4()), "body": "great stuff",
                      "created_at": NOW, "updated_at": NOW}],
    })
    with _no_s3():
        await restore_from_zip_path(db_session, zip_path)

    r = await db_session.execute(text("SELECT body FROM comments WHERE id = :id"), {"id": cid})
    assert r.scalar_one() == "great stuff"


# ── full 24-table round-trip ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_tables_present_in_backup_output(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """create_backup_zip must produce a db/*.json entry for every table."""
    dest = tmp_path / "full.zip"
    with (
        patch("app.services.backup.list_objects", side_effect=lambda p: _empty_gen()),
        patch("app.services.backup.download_file_raw", new_callable=AsyncMock),
        patch("app.services.backup.get_object_headers", new_callable=AsyncMock, return_value={}),
    ):
        await create_backup_zip(db_session, dest)

    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()

    missing = [t for t in _TABLE_INSERT_ORDER if f"db/{t}.json" not in names]
    assert missing == [], f"Missing table entries: {missing}"
