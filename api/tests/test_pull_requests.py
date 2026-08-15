"""Tests for the batch pull request system."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.upload import Upload
from app.models.user import User, UserRole
from app.services.pr import topo_sort_operations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
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
    directory_id: uuid.UUID,
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
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def mock_pr_deps(mock_redis):
    """Mock external dependencies for PR creation (MinIO check and Redis scan cache)."""
    with patch("app.services.pr.object_exists", new_callable=AsyncMock) as m_exists:
        m_exists.return_value = True

        # Mock redis.get to return something for scan checks (meaning scanned clean)
        # We use a side effect that only returns "clean" for scanned keys
        async def mock_get(key: str) -> str | None:
            if "scanned" in key:
                return '{"file_key": "dummy", "size": 1024, "mime_type": "application/pdf"}'
            return None

        mock_redis.get.side_effect = mock_get

        yield m_exists, mock_redis


# ---------------------------------------------------------------------------
# Unit tests for topo_sort_operations
# ---------------------------------------------------------------------------


class TestTopoSort:
    def test_no_dependencies(self) -> None:
        ops = [
            {"op": "edit_material", "material_id": str(uuid.uuid4())},
            {"op": "edit_material", "material_id": str(uuid.uuid4())},
        ]
        result = topo_sort_operations(ops)
        assert result == ops

    def test_directory_before_material(self) -> None:
        ops = [
            {
                "op": "create_material",
                "temp_id": "$mat-1",
                "directory_id": "$dir-1",
                "title": "File",
                "type": "document",
            },
            {
                "op": "create_directory",
                "temp_id": "$dir-1",
                "name": "Folder",
            },
        ]
        result = topo_sort_operations(ops)
        # directory should come first
        assert result[0]["temp_id"] == "$dir-1"
        assert result[1]["temp_id"] == "$mat-1"

    def test_chain_dependency(self) -> None:
        ops = [
            {
                "op": "create_material",
                "directory_id": "$dir-2",
                "title": "File",
                "type": "document",
            },
            {
                "op": "create_directory",
                "temp_id": "$dir-2",
                "parent_id": "$dir-1",
                "name": "Sub",
            },
            {
                "op": "create_directory",
                "temp_id": "$dir-1",
                "name": "Root",
            },
        ]
        result = topo_sort_operations(ops)
        names = [r.get("name") or r.get("title") for r in result]
        assert names == ["Root", "Sub", "File"]

    def test_cyclic_raises(self) -> None:
        from app.core.common.exceptions import BadRequestError

        ops = [
            {
                "op": "create_directory",
                "temp_id": "$a",
                "parent_id": "$b",
                "name": "A",
            },
            {
                "op": "create_directory",
                "temp_id": "$b",
                "parent_id": "$a",
                "name": "B",
            },
        ]
        with pytest.raises(BadRequestError, match="Cyclic dependency"):
            topo_sort_operations(ops)


# ---------------------------------------------------------------------------
# Integration tests for PR creation
# ---------------------------------------------------------------------------


class TestCreateBatchPR:
    async def test_single_create_directory_op(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Test PR Title",
                "description": "Desc",
                "operations": [
                    {
                        "op": "create_directory",
                        "name": "NewDir",
                        "description": "A folder",
                    }
                ],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test PR Title"
        assert len(data["payload"]) == 1
        assert data["payload"][0]["op"] == "create_directory"
        assert data["payload"][0]["name"] == "NewDir"

    async def test_multi_op_batch(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        d = await _create_directory(db_session, "ExistingDir")
        m = await _create_material(db_session, d.id, "ExistingMat", user.id)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Multi-edit batch long",
                "operations": [
                    {"op": "edit_material", "material_id": str(m.id), "title": "Renamed"},
                    {"op": "create_directory", "name": "NewSub", "parent_id": str(d.id)},
                ],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert len(data["payload"]) == 2
        assert set(data["summary_types"]) == {"create_directory", "edit_material"}

    async def test_temp_id_batch_with_auto_approve(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Bureau users get auto-approved; this tests temp_id resolution end-to-end."""
        user = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Folder + file batch long",
                "operations": [
                    {
                        "op": "create_directory",
                        "temp_id": "$dir-1",
                        "name": "BatchFolder",
                    },
                    {
                        "op": "create_material",
                        "temp_id": "$mat-1",
                        "directory_id": "$dir-1",
                        "title": "BatchFile",
                        "type": "document",
                    },
                ],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "approved"

        # Verify items were actually created in DB
        result_dir = await db_session.execute(
            select(Directory).where(Directory.name == "BatchFolder")
        )
        folder = result_dir.scalar_one()
        assert folder is not None

        result_mat = await db_session.execute(select(Material).where(Material.title == "BatchFile"))
        file = result_mat.scalar_one()
        assert file.directory_id == folder.id

    async def test_duplicate_temp_id_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Bad PR Title",
                "operations": [
                    {"op": "create_directory", "temp_id": "$dup", "name": "Dir1"},
                    {"op": "create_directory", "temp_id": "$dup", "name": "Dir2"},
                ],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 422

    async def test_empty_operations_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await _create_user(db_session)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={"title": "Empty Title", "operations": []},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 422

    async def test_five_pr_limit(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        await db_session.commit()

        # Create 5 PRs
        for i in range(5):
            resp = await client.post(
                "/api/pull-requests",
                json={
                    "title": f"PR {i} long",
                    "operations": [{"op": "create_directory", "name": f"Dir {i}"}],
                },
                headers=_auth_headers(user),
            )
            assert resp.status_code == 201

        # 6th should fail
        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "PR 6 long",
                "operations": [{"op": "create_directory", "name": "Dir 6"}],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 400
        assert "5 contributions pending" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Approve/Reject & Execution
# ---------------------------------------------------------------------------


class TestApproveReject:
    async def test_approve_executes_batch(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Target")
        await db_session.commit()

        # Create PR
        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Batch PR Long",
                "operations": [
                    {
                        "op": "create_material",
                        "directory_id": str(d.id),
                        "title": "NewMat",
                        "type": "document",
                    },
                    {
                        "op": "edit_directory",
                        "directory_id": str(d.id),
                        "description": "Updated",
                    },
                ],
            },
            headers=_auth_headers(student),
        )
        assert resp.status_code == 201, resp.text
        pr_id = resp.json()["id"]

        # Approve it
        resp = await client.post(
            f"/api/pull-requests/{pr_id}/approve",
            headers=_auth_headers(mod),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify side effects
        await db_session.refresh(d)
        assert d.description == "Updated"

        res = await db_session.execute(select(Material).where(Material.title == "NewMat"))
        assert res.scalar_one().directory_id == d.id

    @patch("app.services.pr.object_exists", new_callable=AsyncMock)
    @patch("app.core.storage.facade.delete_object", new_callable=AsyncMock)
    async def test_reject_cleans_up_files(
        self,
        mock_delete: AsyncMock,
        mock_exists: AsyncMock,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mock_exists.return_value = True
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir", user_id=student.id)
        await db_session.commit()

        file_key = f"uploads/{student.id}/{uuid.uuid4()}/test.pdf"

        # The PR endpoint verifies that file_key exists in Upload table with status 'clean'
        upload_id_str = file_key.split("/")[2]
        upload_row = Upload(
            upload_id=upload_id_str,
            user_id=student.id,
            quarantine_key=f"quarantine/{student.id}/{upload_id_str}/test.pdf",
            final_key=file_key,
            status="clean",
            filename="test.pdf",
        )
        db_session.add(upload_row)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "With file long",
                "operations": [
                    {
                        "op": "create_material",
                        "directory_id": str(d.id),
                        "title": "FileItem",
                        "type": "document",
                        "file_key": file_key,
                    },
                ],
            },
            headers=_auth_headers(student),
        )
        assert resp.status_code == 201, resp.text
        pr_id = resp.json()["id"]

        resp = await client.post(
            f"/api/pull-requests/{pr_id}/reject",
            json={"reason": "This file does not meet quality standards for the platform."},
            headers=_auth_headers(mod),
        )
        assert resp.status_code == 200

        jobs = db_session.info.get("post_commit_jobs", [])
        assert any(j[0] == "delete_storage_objects" and j[1] == [file_key] for j in jobs)


class TestListAndGet:
    async def test_list_prs(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.STUDENT)
        await db_session.commit()

        await client.post(
            "/api/pull-requests",
            json={
                "title": "PR 1 long",
                "operations": [{"op": "create_directory", "name": "D1"}],
            },
            headers=_auth_headers(user),
        )
        await client.post(
            "/api/pull-requests",
            json={
                "title": "PR 2 long",
                "operations": [{"op": "create_directory", "name": "D2"}],
            },
            headers=_auth_headers(user),
        )

        resp = await client.get("/api/pull-requests", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

        titles = [p["title"] for p in data]
        assert "PR 1 long" in titles
        assert "PR 2 long" in titles

    async def test_get_pr_by_id(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "FetchMe Long",
                "operations": [{"op": "create_directory", "name": "D"}],
            },
            headers=_auth_headers(user),
        )
        pr_id = resp.json()["id"]

        resp = await client.get(f"/api/pull-requests/{pr_id}", headers=_auth_headers(user))
        assert resp.status_code == 200
        assert resp.json()["title"] == "FetchMe Long"

    async def test_get_nonexistent_pr(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        await db_session.commit()
        resp = await client.get(f"/api/pull-requests/{uuid.uuid4()}", headers=_auth_headers(user))
        assert resp.status_code == 404


class TestComments:
    async def test_add_comment(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "CommentMe Long",
                "operations": [{"op": "create_directory", "name": "D"}],
            },
            headers=_auth_headers(user),
        )
        pr_id = resp.json()["id"]

        resp = await client.post(
            f"/api/pull-requests/{pr_id}/comments",
            json={"body": "Nice PR!"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "Nice PR!"

    async def test_list_comments(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Valid PR Title Long",
                "operations": [{"op": "create_directory", "name": "D"}],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201, resp.text
        pr_id = resp.json()["id"]

        await client.post(
            f"/api/pull-requests/{pr_id}/comments",
            json={"body": "C1"},
            headers=_auth_headers(user),
        )

        resp = await client.get(f"/api/pull-requests/{pr_id}/comments", headers=_auth_headers(user))
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestDeleteOperations:
    async def test_delete_material_op(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session)
        m = await _create_material(db_session, d.id)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Delete Mat Long",
                "operations": [{"op": "delete_material", "material_id": str(m.id)}],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "approved"

        # Check it's gone
        res = await db_session.execute(select(Material).where(Material.id == m.id))
        assert res.scalar_one_or_none() is None

    async def test_delete_directory_op(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "ToBeDeleted")
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Delete Dir Long",
                "operations": [{"op": "delete_directory", "directory_id": str(d.id)}],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201

        res = await db_session.execute(select(Directory).where(Directory.id == d.id))
        assert res.scalar_one_or_none() is None


class TestMoveOperation:
    async def test_move_material(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        d1 = await _create_directory(db_session, "D1")
        d2 = await _create_directory(db_session, "D2")
        m = await _create_material(db_session, d1.id)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Move Mat Long",
                "operations": [
                    {
                        "op": "move_item",
                        "target_type": "material",
                        "target_id": str(m.id),
                        "new_parent_id": str(d2.id),
                    }
                ],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201

        await db_session.refresh(m)
        assert m.directory_id == d2.id


class TestEditOperations:
    async def test_edit_material(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session)
        m = await _create_material(db_session, d.id, title="Old")
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Edit Mat Long",
                "operations": [
                    {
                        "op": "edit_material",
                        "material_id": str(m.id),
                        "title": "New Title",
                        "description": "New Desc",
                    }
                ],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201

        await db_session.refresh(m)
        assert m.title == "New Title"
        assert m.description == "New Desc"

    async def test_edit_directory(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, name="OldDir")
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Edit Dir Long",
                "operations": [
                    {
                        "op": "edit_directory",
                        "directory_id": str(d.id),
                        "name": "NewDirName",
                    }
                ],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201

        await db_session.refresh(d)
        assert d.name == "NewDirName"


class TestModeratorAutoApprove:
    @pytest.mark.asyncio
    async def test_moderator_auto_approve(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Test that moderators get auto-approved if auto_approve is True."""
        # Create a moderator user with auto_approve enabled (default is True)
        user = await _create_user(db_session, UserRole.MODERATOR)
        user.auto_approve = True
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Moderator Auto-Approve Test",
                "description": "This should be auto-approved",
                "operations": [
                    {
                        "op": "create_directory",
                        "name": "ModDir",
                    }
                ],
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "approved"
        assert data["author_id"] == str(user.id)
