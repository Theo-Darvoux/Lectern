"""Comprehensive tests for the Pull Request system, covering edge cases and specialized logic."""

import typing
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PRComment, PullRequest
from app.models.upload import Upload
from app.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_user(
    db: AsyncSession, role: UserRole = UserRole.STUDENT, auto_approve: bool = False
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@telecom-sudparis.eu",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
        auto_approve=auto_approve,
    )
    db.add(user)
    await db.flush()
    return user


async def _create_directory(
    db: AsyncSession,
    name: str = "TestDir",
    parent_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> Directory:
    d = Directory(
        id=uuid.uuid4(),
        name=name,
        slug=name.lower().replace(" ", "-"),
        type="folder",
        parent_id=parent_id,
        created_by=user_id,
    )
    db.add(d)
    await db.flush()
    return d


async def _create_material(
    db: AsyncSession,
    directory_id: uuid.UUID | None,
    title: str = "TestMat",
    author_id: uuid.UUID | None = None,
) -> Material:
    m = Material(
        id=uuid.uuid4(),
        directory_id=directory_id,
        title=title,
        slug=title.lower().replace(" ", "-"),
        type="document",
        author_id=author_id,
        tags=[],
    )
    db.add(m)
    await db.flush()
    return m


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def mock_pr_deps(
    mock_redis: AsyncMock,
) -> typing.Generator[tuple[AsyncMock, AsyncMock], None, None]:
    """Mock external dependencies for PR creation."""
    with patch("app.services.pr.object_exists", new_callable=AsyncMock) as m_exists:
        m_exists.return_value = True

        async def mock_get(key: str) -> str | None:
            if "scanned" in key:
                return '{"file_key": "dummy", "size": 1024, "mime_type": "application/pdf"}'
            return None

        mock_redis.get.side_effect = mock_get
        yield m_exists, mock_redis


# ---------------------------------------------------------------------------
# Circular Move Prevention
# ---------------------------------------------------------------------------


class TestCircularMove:
    async def test_move_directory_into_itself(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _create_user(db_session, UserRole.BUREAU, auto_approve=True)
        d = await _create_directory(db_session, "Target")
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Invalid Move",
                "operations": [
                    {
                        "op": "move_item",
                        "target_type": "directory",
                        "target_id": str(d.id),
                        "new_parent_id": str(d.id),
                    }
                ],
            },
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 400
        assert "into itself" in resp.json()["detail"]

    async def test_move_directory_into_descendant(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _create_user(db_session, UserRole.BUREAU, auto_approve=True)
        parent = await _create_directory(db_session, "Parent")
        child = await _create_directory(db_session, "Child", parent_id=parent.id)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Circular Move",
                "operations": [
                    {
                        "op": "move_item",
                        "target_type": "directory",
                        "target_id": str(parent.id),
                        "new_parent_id": str(child.id),
                    }
                ],
            },
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 400
        assert "own descendants" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Optimistic Locking
# ---------------------------------------------------------------------------


class TestOptimisticLocking:
    async def test_edit_material_conflict(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        vieux = await _create_user(db_session, UserRole.VIEUX, auto_approve=True)
        d = await _create_directory(db_session)
        m = await _create_material(db_session, d.id)
        # Create a version with version_lock=5
        mv = MaterialVersion(
            id=uuid.uuid4(),
            material_id=m.id,
            version_number=1,
            file_key="cas/abc",
            file_size=100,
            file_mime_type="text/plain",
            version_lock=5,
            virus_scan_result="clean",
        )
        db_session.add(mv)
        # We need an Upload row for the admin to use this file_key
        u_row = Upload(
            upload_id=str(uuid.uuid4()),
            user_id=vieux.id,
            final_key="cas/abc",
            status="clean",
            filename="test.txt",
        )
        db_session.add(u_row)
        await db_session.commit()

        # Try to edit with version_lock=4 (stale)
        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Stale Edit",
                "operations": [
                    {
                        "op": "edit_material",
                        "material_id": str(m.id),
                        "title": "Conflict",
                        "version_lock": 4,
                        "file_key": "cas/abc",
                    }
                ],
            },
            headers=_auth_headers(vieux),
        )
        assert resp.status_code == 409

    async def test_edit_material_missing_lock_conflict(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        vieux = await _create_user(db_session, UserRole.VIEUX, auto_approve=True)
        d = await _create_directory(db_session)
        m = await _create_material(db_session, d.id)
        # New material has current_version=1.
        # Create version 1 with version_lock=0
        mv = MaterialVersion(
            id=uuid.uuid4(),
            material_id=m.id,
            version_number=1,
            file_key="cas/xyz",
            file_size=100,
            file_mime_type="text/plain",
            version_lock=0,
            virus_scan_result="clean",
        )
        db_session.add(mv)
        u_row = Upload(
            upload_id=str(uuid.uuid4()),
            user_id=vieux.id,
            final_key="cas/xyz",
            status="clean",
            filename="test.txt",
        )
        db_session.add(u_row)
        await db_session.commit()

        # Try to edit with version_lock=None (should fail because material has 0)
        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Missing Lock",
                "operations": [
                    {
                        "op": "edit_material",
                        "material_id": str(m.id),
                        "title": "New Title",
                        "version_lock": None,
                        "file_key": "cas/xyz",
                    }
                ],
            },
            headers=_auth_headers(vieux),
        )
        assert resp.status_code == 409
        assert "expected version_lock=None, found 0" in resp.json()["detail"]

    async def test_material_get_includes_version_lock(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        d = await _create_directory(db_session)
        m = await _create_material(db_session, d.id)
        mv = MaterialVersion(
            id=uuid.uuid4(),
            material_id=m.id,
            version_number=1,
            file_key="cas/foo",
            version_lock=42,
            virus_scan_result="clean",
        )
        db_session.add(mv)
        await db_session.commit()

        resp = await client.get(f"/api/materials/{m.id}", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_version_info"]["version_lock"] == 42

    async def test_edit_material_success_correct_lock(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        vieux = await _create_user(db_session, UserRole.VIEUX, auto_approve=True)
        d = await _create_directory(db_session)
        m = await _create_material(db_session, d.id)
        mv = MaterialVersion(
            id=uuid.uuid4(),
            material_id=m.id,
            version_number=1,
            file_key="cas/ok",
            version_lock=7,
            virus_scan_result="clean",
        )
        db_session.add(mv)
        u_row = Upload(
            upload_id=str(uuid.uuid4()),
            user_id=vieux.id,
            final_key="cas/ok",
            status="clean",
            filename="ok.txt",
        )
        db_session.add(u_row)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Valid Edit",
                "operations": [
                    {
                        "op": "edit_material",
                        "material_id": str(m.id),
                        "title": "Updated",
                        "version_lock": 7,
                        "file_key": "cas/ok",
                    }
                ],
            },
            headers=_auth_headers(vieux),
        )
        assert resp.status_code == 201
        # Verify it was applied and lock incremented
        await db_session.refresh(m)
        assert m.current_version == 2
        # Check new version lock
        result = await db_session.execute(
            select(MaterialVersion).where(
                MaterialVersion.material_id == m.id, MaterialVersion.version_number == 2
            )
        )
        new_mv = result.scalar_one()
        assert new_mv.version_lock == 8


# ---------------------------------------------------------------------------
# File Claiming & Ownership
# ---------------------------------------------------------------------------


class TestFileClaiming:
    async def test_double_claim_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user1 = await _create_user(db_session, UserRole.STUDENT)
        user2 = await _create_user(db_session, UserRole.STUDENT)
        file_key = f"cas/{uuid.uuid4().hex}"

        # We need a 'clean' upload row for both
        for u in [user1, user2]:
            u_row = Upload(
                upload_id=str(uuid.uuid4()),
                user_id=u.id,
                final_key=file_key,
                status="clean",
                filename="test.pdf",
            )
            db_session.add(u_row)
        await db_session.commit()

        # PR 1 claims it
        resp1 = await client.post(
            "/api/pull-requests",
            json={
                "title": "PR 1",
                "operations": [
                    {
                        "op": "create_material",
                        "title": "M1",
                        "type": "document",
                        "file_key": file_key,
                    }
                ],
            },
            headers=_auth_headers(user1),
        )
        assert resp1.status_code == 201

        # PR 2 tries to claim it
        resp2 = await client.post(
            "/api/pull-requests",
            json={
                "title": "PR 2",
                "operations": [
                    {
                        "op": "create_material",
                        "title": "M2",
                        "type": "document",
                        "file_key": file_key,
                    }
                ],
            },
            headers=_auth_headers(user2),
        )
        assert resp2.status_code == 400
        assert "already included" in resp2.json()["detail"]

    async def test_file_ownership_enforcement(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user1 = await _create_user(db_session, UserRole.STUDENT)
        user2 = await _create_user(db_session, UserRole.STUDENT)
        # user1 tries to use user2's file_key
        bad_key = f"uploads/{user2.id}/stolen.pdf"

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Stolen File",
                "operations": [
                    {"op": "create_material", "title": "X", "type": "document", "file_key": bad_key}
                ],
            },
            headers=_auth_headers(user1),
        )
        assert resp.status_code == 400
        assert "does not belong" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# RBAC & Access Control
# ---------------------------------------------------------------------------


class TestRBAC:
    async def test_student_cannot_approve(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        await _create_user(db_session, UserRole.BUREAU)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status="open",
            title="T",
            payload=[],
            author_id=student.id,
        )
        db_session.add(pr)
        await db_session.commit()

        resp = await client.post(
            f"/api/pull-requests/{pr.id}/approve", headers=_auth_headers(student)
        )
        assert resp.status_code == 403

    async def test_author_can_cancel(self, client: AsyncClient, db_session: AsyncSession) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status="open",
            title="To Cancel",
            payload=[],
            author_id=student.id,
        )
        db_session.add(pr)
        await db_session.commit()

        resp = await client.post(
            f"/api/pull-requests/{pr.id}/cancel", headers=_auth_headers(student)
        )
        assert resp.status_code == 200

        await db_session.refresh(pr)
        assert pr.status == "cancelled"

    async def test_non_author_cannot_cancel(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user1 = await _create_user(db_session, UserRole.STUDENT)
        user2 = await _create_user(db_session, UserRole.STUDENT)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status="open",
            title="Protected",
            payload=[],
            author_id=user1.id,
        )
        db_session.add(pr)
        await db_session.commit()

        resp = await client.post(f"/api/pull-requests/{pr.id}/cancel", headers=_auth_headers(user2))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Specialized Endpoints
# ---------------------------------------------------------------------------


class TestSpecializedEndpoints:
    async def test_list_for_item(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        d = await _create_directory(db_session)
        # Create a PR affecting this directory
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status="open",
            title="PR for Dir",
            payload=[{"op": "edit_directory", "directory_id": str(d.id), "description": "New"}],
            author_id=user.id,
        )
        db_session.add(pr)
        await db_session.commit()

        resp = await client.get(
            f"/api/pull-requests/for-item?targetType=directory&targetId={d.id}",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "PR for Dir"

    async def test_get_diff(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        d = await _create_directory(db_session, "Old")
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status="open",
            title="Diff Test",
            payload=[
                {
                    "op": "create_material",
                    "directory_id": str(d.id),
                    "title": "M1",
                    "type": "document",
                    "file_key": "cas/abc",
                }
            ],
            author_id=user.id,
        )
        db_session.add(pr)
        await db_session.commit()

        resp = await client.get(f"/api/pull-requests/{pr.id}/diff", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert "diff" in data and data["diff"] is not None

    async def test_get_preview(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status="open",
            title="Preview Test",
            payload=[
                {
                    "op": "create_material",
                    "title": "PreMat",
                    "type": "document",
                    "file_key": "cas/abc",
                    "file_name": "test.pdf",
                    "file_mime_type": "application/pdf",
                }
            ],
            author_id=user.id,
        )
        db_session.add(pr)
        await db_session.commit()

        resp = await client.get(
            f"/api/pull-requests/{pr.id}/preview?opIndex=0", headers=_auth_headers(user)
        )
        assert resp.status_code == 200
        assert resp.json()["file_name"] == "test.pdf"


# ---------------------------------------------------------------------------
# Operation Constraints
# ---------------------------------------------------------------------------


class TestConstraints:
    async def test_attachment_nesting_limit(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        d = await _create_directory(db_session)
        grandparent_mat = await _create_material(db_session, d.id, "GrandParent")
        parent_mat = await _create_material(db_session, d.id, "Parent")
        parent_mat.parent_material_id = grandparent_mat.id
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Deep Nesting",
                "operations": [
                    {
                        "op": "create_material",
                        "title": "Child",
                        "type": "document",
                        "parent_material_id": str(parent_mat.id),
                    }
                ],
            },
            headers=_auth_headers(user),
        )
        print(f"DEBUG Nesting: status={resp.status_code}, detail={resp.json().get('detail')}")
        assert resp.status_code == 400
        assert "Cannot attach" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Recursive Reindexing
# ---------------------------------------------------------------------------


class TestReindexing:
    async def test_move_directory_triggers_recursive_reindex(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _create_user(db_session, UserRole.BUREAU, auto_approve=True)
        root = await _create_directory(db_session, "Root")
        dest = await _create_directory(db_session, "Dest")
        child = await _create_directory(db_session, "Child", parent_id=root.id)
        mat = await _create_material(db_session, child.id)
        await db_session.commit()

        db_session.info["post_commit_jobs"] = []

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Move Reindex",
                "operations": [
                    {
                        "op": "move_item",
                        "target_type": "directory",
                        "target_id": str(child.id),
                        "new_parent_id": str(dest.id),
                    }
                ],
            },
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        jobs = db_session.info.get("post_commit_jobs", [])
        job_types = [j[0] for j in jobs]
        assert "index_directory" in job_types
        # Verify both child and its material are enqueued
        queued_ids = [str(j[1]) for j in jobs if j[0] in ("index_directory", "index_material")]
        assert str(child.id) in queued_ids
        assert str(mat.id) in queued_ids


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class TestNotifications:
    async def test_comment_reply_notifies(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user1 = await _create_user(db_session)
        user2 = await _create_user(db_session)
        pr = PullRequest(
            id=uuid.uuid4(), type="batch", status="open", title="T", payload=[], author_id=user1.id
        )
        db_session.add(pr)
        await db_session.flush()

        c1 = PRComment(id=uuid.uuid4(), pr_id=pr.id, author_id=user1.id, body="Hey")
        db_session.add(c1)
        await db_session.commit()

        with patch("app.services.notification.notify_user", new_callable=AsyncMock) as mock_notify:
            resp = await client.post(
                f"/api/pull-requests/{pr.id}/comments",
                json={"body": "Reply", "parent_id": str(c1.id)},
                headers=_auth_headers(user2),
            )
            assert resp.status_code == 200
            mock_notify.assert_called_once()
            assert mock_notify.call_args[0][1] == user1.id
            assert "replied to your comment" in mock_notify.call_args[0][3]
