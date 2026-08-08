"""Tests for PR revert feature: soft-delete, pre-state snapshots, revert endpoint, and UI contract."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.services.pr import (
    _build_reverse_ops,
)

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


async def _create_material_version(
    db: AsyncSession,
    material_id: uuid.UUID,
    version_number: int = 1,
    file_key: str | None = None,
) -> MaterialVersion:
    mv = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material_id,
        version_number=version_number,
        file_key=file_key or f"cas/{uuid.uuid4().hex}",
        file_name="test.pdf",
        file_size=1024,
        file_mime_type="application/pdf",
        virus_scan_result="clean",
    )
    db.add(mv)
    await db.flush()
    return mv


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def _create_and_approve_pr(
    client: AsyncClient,
    db_session: AsyncSession,
    student: User,
    mod: User,
    operations: list[dict],
    title: str = "Test PR Long Title",
) -> dict:
    """Helper: create a PR as student, approve as moderator, return response JSON."""
    resp = await client.post(
        "/api/pull-requests",
        json={"title": title, "operations": operations},
        headers=_auth_headers(student),
    )
    assert resp.status_code == 201, resp.text
    pr_id = resp.json()["id"]

    resp = await client.post(
        f"/api/pull-requests/{pr_id}/approve",
        headers=_auth_headers(mod),
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"/api/pull-requests/{pr_id}", headers=_auth_headers(mod))
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture(autouse=True)
def mock_pr_deps(mock_redis):
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
# Phase 1: Model properties
# ---------------------------------------------------------------------------


class TestPullRequestModelProperties:
    """Test computed properties on the PullRequest model."""

    async def test_revert_grace_expires_at_approved(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        now = datetime.now(UTC)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status=PRStatus.APPROVED,
            title="Test PR Long Title",
            payload=[],
            summary_types=[],
            author_id=user.id,
            approved_at=now,
            applied_result=[],
        )
        db_session.add(pr)
        await db_session.flush()

        assert pr.revert_grace_expires_at is not None
        expected = now + timedelta(days=7)
        assert abs((pr.revert_grace_expires_at - expected).total_seconds()) < 1

    async def test_revert_grace_expires_at_none_for_open(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.STUDENT)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status=PRStatus.OPEN,
            title="Open PR Long Title",
            payload=[],
            summary_types=[],
            author_id=user.id,
        )
        db_session.add(pr)
        await db_session.flush()

        assert pr.revert_grace_expires_at is None

    async def test_is_revertable_true(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status=PRStatus.APPROVED,
            title="Revertable PR Title",
            payload=[],
            summary_types=[],
            author_id=user.id,
            approved_at=datetime.now(UTC),
            applied_result=[{"op": "create_directory", "result_id": str(uuid.uuid4())}],
        )
        db_session.add(pr)
        await db_session.flush()

        assert pr.is_revertable is True

    async def test_is_revertable_false_revert_type(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="revert",
            status=PRStatus.APPROVED,
            title="Revert PR Title",
            payload=[],
            summary_types=[],
            author_id=user.id,
            approved_at=datetime.now(UTC),
            applied_result=[],
        )
        db_session.add(pr)
        await db_session.flush()

        assert pr.is_revertable is False

    async def test_is_revertable_false_expired(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status=PRStatus.APPROVED,
            title="Expired PR Title",
            payload=[],
            summary_types=[],
            author_id=user.id,
            approved_at=datetime.now(UTC) - timedelta(days=8),
            applied_result=[{"op": "create_directory"}],
        )
        db_session.add(pr)
        await db_session.flush()

        assert pr.is_revertable is False

    async def test_is_revertable_false_already_reverted(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        # We need a real PR in the DB to satisfy the FK constraint for reverted_by_pr_id
        revert_pr = PullRequest(
            id=uuid.uuid4(),
            type="revert",
            status=PRStatus.OPEN,
            title="Revert PR",
            payload=[],
            summary_types=[],
            author_id=user.id,
        )
        db_session.add(revert_pr)
        await db_session.flush()

        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status=PRStatus.APPROVED,
            title="Already Reverted PR",
            payload=[],
            summary_types=[],
            author_id=user.id,
            approved_at=datetime.now(UTC),
            applied_result=[{"op": "create_directory"}],
            reverted_by_pr_id=revert_pr.id,
        )
        db_session.add(pr)
        await db_session.flush()

        assert pr.is_revertable is False

    async def test_is_revertable_false_no_applied_result(self, db_session: AsyncSession) -> None:
        user = await _create_user(db_session, UserRole.BUREAU)
        pr = PullRequest(
            id=uuid.uuid4(),
            type="batch",
            status=PRStatus.APPROVED,
            title="No Applied Result",
            payload=[],
            summary_types=[],
            author_id=user.id,
            approved_at=datetime.now(UTC),
            applied_result=None,
        )
        db_session.add(pr)
        await db_session.flush()

        assert pr.is_revertable is False


# ---------------------------------------------------------------------------
# Phase 2: Soft-delete
# ---------------------------------------------------------------------------


class TestSoftDelete:
    """Test that delete operations soft-delete rather than hard-delete."""

    async def test_delete_material_sets_deleted_at(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir", user_id=student.id)
        m = await _create_material(db_session, d.id, "ToDelete", student.id)
        mat_id = m.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "delete_material", "material_id": str(mat_id)}],
        )
        assert pr_data["status"] == "approved"

        # Normal query should NOT find it (global filter)
        result = await db_session.execute(select(Material).where(Material.id == mat_id))
        assert result.scalar_one_or_none() is None

        # include_deleted should find it
        result = await db_session.execute(
            select(Material).where(Material.id == mat_id).execution_options(include_deleted=True)
        )
        mat = result.scalar_one()
        assert mat.deleted_at is not None

    async def test_delete_directory_soft_deletes_subtree(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        parent = await _create_directory(db_session, "Parent", user_id=student.id)
        child = await _create_directory(
            db_session, "Child", parent_id=parent.id, user_id=student.id
        )
        m = await _create_material(db_session, child.id, "ChildMat", student.id)
        parent_id = parent.id
        child_id = child.id
        mat_id = m.id
        await db_session.commit()

        await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "delete_directory", "directory_id": str(parent_id)}],
        )

        # All should be invisible to normal queries
        for model, item_id in [(Directory, parent_id), (Directory, child_id), (Material, mat_id)]:
            result = await db_session.execute(select(model).where(model.id == item_id))
            assert result.scalar_one_or_none() is None, (
                f"{model.__name__} {item_id} should be hidden"
            )

        # All should be visible with include_deleted
        for model, item_id in [(Directory, parent_id), (Directory, child_id), (Material, mat_id)]:
            result = await db_session.execute(
                select(model).where(model.id == item_id).execution_options(include_deleted=True)  # type: ignore
            )
            row = result.scalar_one()
            assert row.deleted_at is not None, (
                f"{model.__name__} {item_id} should have deleted_at set"
            )  # type: ignore

    async def test_soft_deleted_material_versions_hidden(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir")
        m = await _create_material(db_session, d.id, "WithVersion", student.id)
        mv = await _create_material_version(db_session, m.id)
        mat_id = m.id
        mv_id = mv.id
        await db_session.commit()

        await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "delete_material", "material_id": str(mat_id)}],
        )

        result = await db_session.execute(
            select(MaterialVersion).where(MaterialVersion.id == mv_id)
        )
        assert result.scalar_one_or_none() is None

        result = await db_session.execute(
            select(MaterialVersion)
            .where(MaterialVersion.id == mv_id)
            .execution_options(include_deleted=True)
        )
        assert result.scalar_one().deleted_at is not None

    async def test_no_cas_ref_decrement_on_soft_delete(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Soft-delete should NOT decrement CAS refs or schedule file deletion."""
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir")
        m = await _create_material(db_session, d.id, "CASMat", student.id)
        await _create_material_version(db_session, m.id, file_key="cas/abc123")
        mat_id = m.id
        await db_session.commit()

        db_session.info["post_commit_jobs"] = []
        await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "delete_material", "material_id": str(mat_id)}],
        )

        jobs = db_session.info.get("post_commit_jobs", [])
        delete_jobs = [j for j in jobs if j[0] == "delete_storage_objects"]
        assert len(delete_jobs) == 0, "Soft-delete should not schedule file deletion"


# ---------------------------------------------------------------------------
# Phase 3: Pre-state snapshots
# ---------------------------------------------------------------------------


class TestPreStateSnapshot:
    """Test that apply_pr captures pre_state for editable/movable ops."""

    async def test_edit_material_captures_pre_state(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir")
        m = await _create_material(db_session, d.id, "OrigTitle", student.id)
        mat_id = m.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "edit_material", "material_id": str(mat_id), "title": "NewTitle"}],
        )

        applied = pr_data.get("applied_result", [])
        assert len(applied) == 1
        pre = applied[0].get("pre_state")
        assert pre is not None
        assert pre["title"] == "OrigTitle"
        assert pre["slug"] == "origtitle"

    async def test_edit_directory_captures_pre_state(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "OldName", user_id=student.id)
        dir_id = d.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "edit_directory", "directory_id": str(dir_id), "name": "NewName"}],
        )

        applied = pr_data.get("applied_result", [])
        pre = applied[0].get("pre_state")
        assert pre is not None
        assert pre["name"] == "OldName"
        assert pre["slug"] == "oldname"

    async def test_move_material_captures_pre_state(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d1 = await _create_directory(db_session, "Source")
        d2 = await _create_directory(db_session, "Dest")
        m = await _create_material(db_session, d1.id, "Movable", student.id)
        mat_id = m.id
        d1_id = d1.id
        d2_id = d2.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [
                {
                    "op": "move_item",
                    "target_type": "material",
                    "target_id": str(mat_id),
                    "new_parent_id": str(d2_id),
                }
            ],
        )

        applied = pr_data.get("applied_result", [])
        pre = applied[0].get("pre_state")
        assert pre is not None
        assert pre["target_type"] == "material"
        assert pre["prev_directory_id"] == str(d1_id)

    async def test_move_directory_captures_pre_state(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        root = await _create_directory(db_session, "Root")
        dest = await _create_directory(db_session, "Dest")
        child = await _create_directory(db_session, "Movable", parent_id=root.id)
        child_id = child.id
        root_id = root.id
        dest_id = dest.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [
                {
                    "op": "move_item",
                    "target_type": "directory",
                    "target_id": str(child_id),
                    "new_parent_id": str(dest_id),
                }
            ],
        )

        applied = pr_data.get("applied_result", [])
        pre = applied[0].get("pre_state")
        assert pre is not None
        assert pre["target_type"] == "directory"
        assert pre["prev_parent_id"] == str(root_id)

    async def test_create_ops_have_no_pre_state(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "create_directory", "name": "Fresh"}],
        )

        applied = pr_data.get("applied_result", [])
        assert applied[0].get("pre_state") is None

    async def test_delete_ops_have_no_pre_state(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "ToDelete")
        dir_id = d.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "delete_directory", "directory_id": str(dir_id)}],
        )

        applied = pr_data.get("applied_result", [])
        assert applied[0].get("pre_state") is None

    async def test_approved_at_is_set(self, client: AsyncClient, db_session: AsyncSession) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        mod = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            mod,
            [{"op": "create_directory", "name": "Dir"}],
        )

        assert pr_data.get("approved_at") is not None


# ---------------------------------------------------------------------------
# Phase 4: _build_reverse_ops unit tests
# ---------------------------------------------------------------------------


class TestBuildReverseOps:
    """Test the pure function that generates reverse operations."""

    def test_create_material_reverses_to_delete(self) -> None:
        applied = [{"op": "create_material", "result_id": "abc-123"}]
        reverse = _build_reverse_ops(applied)
        assert len(reverse) == 1
        assert reverse[0]["op"] == "delete_material"
        assert reverse[0]["material_id"] == "abc-123"

    def test_create_directory_reverses_to_delete(self) -> None:
        applied = [{"op": "create_directory", "result_id": "dir-456"}]
        reverse = _build_reverse_ops(applied)
        assert reverse[0]["op"] == "delete_directory"
        assert reverse[0]["directory_id"] == "dir-456"

    def test_delete_material_reverses_to_undelete(self) -> None:
        applied = [{"op": "delete_material", "result_id": "mat-789"}]
        reverse = _build_reverse_ops(applied)
        assert reverse[0]["op"] == "undelete_material"
        assert reverse[0]["material_id"] == "mat-789"

    def test_delete_directory_reverses_to_undelete(self) -> None:
        applied = [{"op": "delete_directory", "result_id": "dir-012"}]
        reverse = _build_reverse_ops(applied)
        assert reverse[0]["op"] == "undelete_directory"
        assert reverse[0]["directory_id"] == "dir-012"

    def test_edit_material_carries_pre_state(self) -> None:
        pre = {"title": "OldTitle", "slug": "oldtitle"}
        applied = [{"op": "edit_material", "result_id": "mat-1", "pre_state": pre}]
        reverse = _build_reverse_ops(applied)
        assert reverse[0]["op"] == "edit_material"
        assert reverse[0]["pre_state"] == pre

    def test_edit_directory_carries_pre_state(self) -> None:
        pre = {"name": "OldName", "slug": "oldname"}
        applied = [{"op": "edit_directory", "result_id": "dir-1", "pre_state": pre}]
        reverse = _build_reverse_ops(applied)
        assert reverse[0]["op"] == "edit_directory"
        assert reverse[0]["pre_state"] == pre

    def test_move_item_carries_pre_state(self) -> None:
        pre = {"target_type": "material", "prev_directory_id": "dir-old"}
        applied = [
            {
                "op": "move_item",
                "result_id": "mat-1",
                "target_type": "material",
                "pre_state": pre,
            }
        ]
        reverse = _build_reverse_ops(applied)
        assert reverse[0]["op"] == "move_item"
        assert reverse[0]["pre_state"] == pre

    def test_reverse_order(self) -> None:
        """Operations should be reversed so undoing happens in the right order."""
        applied = [
            {"op": "create_directory", "result_id": "d1"},
            {"op": "create_material", "result_id": "m1"},
        ]
        reverse = _build_reverse_ops(applied)
        assert reverse[0]["op"] == "delete_material"
        assert reverse[1]["op"] == "delete_directory"

    def test_mixed_ops(self) -> None:
        applied = [
            {"op": "create_directory", "result_id": "d1"},
            {"op": "edit_material", "result_id": "m1", "pre_state": {"title": "X"}},
            {"op": "delete_material", "result_id": "m2"},
        ]
        reverse = _build_reverse_ops(applied)
        assert len(reverse) == 3
        assert reverse[0]["op"] == "undelete_material"
        assert reverse[1]["op"] == "edit_material"
        assert reverse[2]["op"] == "delete_directory"


# ---------------------------------------------------------------------------
# Phase 5+6: Revert endpoint integration tests
# ---------------------------------------------------------------------------


class TestRevertEndpoint:
    """Test the POST /api/pull-requests/{id}/revert endpoint."""

    async def test_revert_create_directory(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Reverting a create_directory PR soft-deletes the directory."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "RevertMe"}],
        )
        pr_id = pr_data["id"]

        # Verify directory exists
        result = await db_session.execute(select(Directory).where(Directory.name == "RevertMe"))
        assert result.scalar_one_or_none() is not None

        # Revert
        resp = await client.post(
            f"/api/pull-requests/{pr_id}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201, resp.text
        revert_data = resp.json()
        assert revert_data["type"] == "revert"
        assert revert_data["status"] == "approved"
        assert revert_data["reverts_pr_id"] == pr_id

        # Directory should be invisible
        result = await db_session.execute(select(Directory).where(Directory.name == "RevertMe"))
        assert result.scalar_one_or_none() is None

        # Original PR should be marked as reverted
        resp = await client.get(f"/api/pull-requests/{pr_id}", headers=_auth_headers(admin))
        assert resp.json()["reverted_by_pr_id"] == revert_data["id"]
        assert resp.json()["can_revert"] is False

    async def test_revert_create_material(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Host")
        dir_id = d.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [
                {
                    "op": "create_material",
                    "directory_id": str(dir_id),
                    "title": "RevertMat",
                    "type": "document",
                }
            ],
        )
        pr_id = pr_data["id"]

        result = await db_session.execute(select(Material).where(Material.title == "RevertMat"))
        assert result.scalar_one_or_none() is not None

        resp = await client.post(
            f"/api/pull-requests/{pr_id}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        result = await db_session.execute(select(Material).where(Material.title == "RevertMat"))
        assert result.scalar_one_or_none() is None

    async def test_revert_delete_material_restores_it(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Reverting a delete_material undeletes the soft-deleted material."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir")
        m = await _create_material(db_session, d.id, "WillBeDeleted", student.id)
        mat_id = m.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "delete_material", "material_id": str(mat_id)}],
        )
        pr_id = pr_data["id"]

        # Confirm deleted
        result = await db_session.execute(select(Material).where(Material.id == mat_id))
        assert result.scalar_one_or_none() is None

        # Revert the delete
        resp = await client.post(
            f"/api/pull-requests/{pr_id}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        # Material should be back
        result = await db_session.execute(select(Material).where(Material.id == mat_id))
        mat = result.scalar_one()
        assert mat.deleted_at is None

    async def test_revert_delete_directory_restores_subtree(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        parent = await _create_directory(db_session, "Parent")
        child = await _create_directory(db_session, "Child", parent_id=parent.id)
        m = await _create_material(db_session, child.id, "ChildMat", student.id)
        parent_id, child_id, mat_id = parent.id, child.id, m.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "delete_directory", "directory_id": str(parent_id)}],
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        # All should be restored
        for model, item_id in [(Directory, parent_id), (Directory, child_id), (Material, mat_id)]:
            result = await db_session.execute(select(model).where(model.id == item_id))
            row = result.scalar_one()
            assert row.deleted_at is None, f"{model.__name__} should be restored"  # type: ignore

    async def test_revert_edit_material_restores_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir")
        m = await _create_material(db_session, d.id, "Original", student.id)
        mat_id = m.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "edit_material", "material_id": str(mat_id), "title": "Changed"}],
        )

        # Verify it was changed
        await db_session.refresh(m)
        assert m.title == "Changed"

        # Revert
        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        await db_session.refresh(m)
        assert m.title == "Original"
        assert m.slug == "original"

    async def test_revert_edit_directory_restores_name(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "OldDirName")
        dir_id = d.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "edit_directory", "directory_id": str(dir_id), "name": "NewDirName"}],
        )

        await db_session.refresh(d)
        assert d.name == "NewDirName"

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        await db_session.refresh(d)
        assert d.name == "OldDirName"
        assert d.slug == "olddirname"

    async def test_revert_move_material_back(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        d1 = await _create_directory(db_session, "Source")
        d2 = await _create_directory(db_session, "Dest")
        m = await _create_material(db_session, d1.id, "Movable", student.id)
        mat_id, d1_id, d2_id = m.id, d1.id, d2.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [
                {
                    "op": "move_item",
                    "target_type": "material",
                    "target_id": str(mat_id),
                    "new_parent_id": str(d2_id),
                }
            ],
        )

        await db_session.refresh(m)
        assert m.directory_id == d2_id

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        await db_session.refresh(m)
        assert m.directory_id == d1_id

    async def test_revert_move_directory_back(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        root = await _create_directory(db_session, "Root")
        dest = await _create_directory(db_session, "Dest")
        child = await _create_directory(db_session, "Movable", parent_id=root.id)
        child_id, root_id, dest_id = child.id, root.id, dest.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [
                {
                    "op": "move_item",
                    "target_type": "directory",
                    "target_id": str(child_id),
                    "new_parent_id": str(dest_id),
                }
            ],
        )

        await db_session.refresh(child)
        assert child.parent_id == dest_id

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        await db_session.refresh(child)
        assert child.parent_id == root_id

    async def test_revert_move_directory_rejects_cycle_created_by_later_move(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        parent = await _create_directory(db_session, "OriginalParent")
        child = await _create_directory(db_session, "OriginalChild", parent_id=parent.id)
        parent_id, child_id = parent.id, child.id
        await db_session.commit()

        first_pr = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [
                {
                    "op": "move_item",
                    "target_type": "directory",
                    "target_id": str(child_id),
                    "new_parent_id": None,
                }
            ],
        )
        await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [
                {
                    "op": "move_item",
                    "target_type": "directory",
                    "target_id": str(parent_id),
                    "new_parent_id": str(child_id),
                }
            ],
        )

        response = await client.post(
            f"/api/pull-requests/{first_pr['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert response.status_code == 400
        assert "own descendants" in response.json()["detail"]

        await db_session.refresh(parent)
        await db_session.refresh(child)
        assert parent.parent_id == child_id
        assert child.parent_id is None

    async def test_revert_multi_op_pr(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Revert a PR with multiple ops: create_dir + create_material + edit_directory."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        existing = await _create_directory(db_session, "Existing")
        existing_id = existing.id
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [
                {
                    "op": "create_directory",
                    "temp_id": "$d1",
                    "name": "NewDir",
                    "parent_id": str(existing_id),
                },
                {
                    "op": "create_material",
                    "directory_id": "$d1",
                    "title": "NewMat",
                    "type": "document",
                },
                {
                    "op": "edit_directory",
                    "directory_id": str(existing_id),
                    "description": "Updated desc",
                },
            ],
            title="Multi-Op PR Title",
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        # Created items should be gone
        result = await db_session.execute(select(Directory).where(Directory.name == "NewDir"))
        assert result.scalar_one_or_none() is None

        result = await db_session.execute(select(Material).where(Material.title == "NewMat"))
        assert result.scalar_one_or_none() is None

        # Edit should be reverted
        await db_session.refresh(existing)
        assert existing.description is None


# ---------------------------------------------------------------------------
# Phase 6: Authorization & guard checks
# ---------------------------------------------------------------------------


class TestRevertGuards:
    """Test that the revert endpoint rejects unauthorized or invalid requests."""

    async def test_moderator_cannot_revert(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        moderator = await _create_user(db_session, UserRole.MODERATOR)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "Guarded"}],
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(moderator),
        )
        assert resp.status_code == 403
        assert "administrator" in resp.json()["detail"].lower()

    async def test_student_cannot_revert(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "Guarded"}],
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(student),
        )
        assert resp.status_code == 403

    async def test_vieux_can_revert(self, client: AsyncClient, db_session: AsyncSession) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        vieux = await _create_user(db_session, UserRole.VIEUX)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "VieuxTest"}],
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(vieux),
        )
        assert resp.status_code == 201

    async def test_cannot_revert_open_pr(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Open PR Long Title",
                "operations": [{"op": "create_directory", "name": "X"}],
            },
            headers=_auth_headers(student),
        )
        pr_id = resp.json()["id"]

        resp = await client.post(
            f"/api/pull-requests/{pr_id}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 400
        assert "approved" in resp.json()["detail"].lower()

    async def test_cannot_revert_rejected_pr(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Will Reject Long",
                "operations": [{"op": "create_directory", "name": "X"}],
            },
            headers=_auth_headers(student),
        )
        pr_id = resp.json()["id"]

        await client.post(
            f"/api/pull-requests/{pr_id}/reject",
            json={"reason": "This does not meet standards for the platform."},
            headers=_auth_headers(admin),
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_id}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 400

    async def test_cannot_revert_a_revert_pr(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Reverts are terminal — cannot be reverted themselves."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "DoubleRevert"}],
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201
        revert_id = resp.json()["id"]

        # Try to revert the revert
        resp = await client.post(
            f"/api/pull-requests/{revert_id}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 400
        assert "revert" in resp.json()["detail"].lower()

    async def test_cannot_revert_already_reverted(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "OnceOnly"}],
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        # Second revert should fail
        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 400
        assert "already" in resp.json()["detail"].lower()

    async def test_cannot_revert_expired_grace(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """After 7 days, the revert grace period has expired."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "ExpiredGrace"}],
        )

        # Manually backdate approved_at
        pr = await db_session.scalar(
            select(PullRequest).where(PullRequest.id == uuid.UUID(pr_data["id"]))
        )
        pr.approved_at = datetime.now(UTC) - timedelta(days=8)
        await db_session.commit()

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 400
        assert "grace" in resp.json()["detail"].lower()

    async def test_revert_nonexistent_pr(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        resp = await client.post(
            f"/api/pull-requests/{uuid.uuid4()}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 7: Response schema contract
# ---------------------------------------------------------------------------


class TestRevertResponseSchema:
    """Test that the PullRequestOut response includes revert fields."""

    async def test_approved_pr_has_revert_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "SchemaTest"}],
        )

        assert "approved_at" in pr_data
        assert "revert_grace_expires_at" in pr_data
        assert "can_revert" in pr_data
        assert "reverts_pr_id" in pr_data
        assert "reverted_by_pr_id" in pr_data
        assert pr_data["approved_at"] is not None
        assert pr_data["revert_grace_expires_at"] is not None
        assert pr_data["can_revert"] is True
        assert pr_data["reverts_pr_id"] is None
        assert pr_data["reverted_by_pr_id"] is None

    async def test_open_pr_has_null_revert_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Open PR Schema Test",
                "operations": [{"op": "create_directory", "name": "X"}],
            },
            headers=_auth_headers(student),
        )
        data = resp.json()

        assert data["approved_at"] is None
        assert data["revert_grace_expires_at"] is None
        assert data["can_revert"] is False

    async def test_revert_pr_has_reverts_pr_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "ForRevertSchema"}],
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        revert = resp.json()

        assert revert["reverts_pr_id"] == pr_data["id"]
        assert revert["type"] == "revert"
        assert revert["can_revert"] is False

    async def test_reverted_pr_shows_reverted_by(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "ForRevertedBy"}],
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        revert_id = resp.json()["id"]

        resp = await client.get(
            f"/api/pull-requests/{pr_data['id']}",
            headers=_auth_headers(admin),
        )
        data = resp.json()
        assert data["reverted_by_pr_id"] == revert_id
        assert data["can_revert"] is False

    async def test_pr_list_includes_revert_prs(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "ForListTest"}],
        )

        await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )

        resp = await client.get(
            "/api/pull-requests?type=revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 200
        data = resp.json()
        revert_prs = [p for p in data if p["type"] == "revert"]
        assert len(revert_prs) >= 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestRevertEdgeCases:
    """Edge cases and data integrity."""

    async def test_revert_pr_title_format(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "TitleCheck"}],
            title="My Cool Contribution",
        )

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        revert = resp.json()
        assert revert["title"] == "Revert: My Cool Contribution"

    async def test_revert_preserves_original_payload(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Original PR's payload should not be mutated by the revert."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "Preserved"}],
        )

        original_payload = pr_data["payload"]

        await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )

        resp = await client.get(
            f"/api/pull-requests/{pr_data['id']}",
            headers=_auth_headers(admin),
        )
        assert resp.json()["payload"] == original_payload

    async def test_auto_approved_pr_is_revertable(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Bureau auto-approved PRs should be revertable like any other."""
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        resp = await client.post(
            "/api/pull-requests",
            json={
                "title": "Auto-approved PR Title",
                "operations": [{"op": "create_directory", "name": "AutoApproved"}],
            },
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201
        pr_data = resp.json()
        assert pr_data["status"] == "approved"

        resp = await client.post(
            f"/api/pull-requests/{pr_data['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        result = await db_session.execute(select(Directory).where(Directory.name == "AutoApproved"))
        assert result.scalar_one_or_none() is None

    async def test_revert_edit_overwrites_later_edits(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """If a later PR edits the same material, revert still restores pre-PR-A state."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir")
        m = await _create_material(db_session, d.id, "V1Title", student.id)
        mat_id = m.id
        await db_session.commit()

        # PR A: edit title to V2
        pr_a = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "edit_material", "material_id": str(mat_id), "title": "V2Title"}],
            title="PR A Long Title",
        )

        # PR B: edit title to V3
        await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "edit_material", "material_id": str(mat_id), "title": "V3Title"}],
            title="PR B Long Title",
        )

        await db_session.refresh(m)
        assert m.title == "V3Title"

        # Revert PR A — should go back to V1, destroying V3
        resp = await client.post(
            f"/api/pull-requests/{pr_a['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        await db_session.refresh(m)
        assert m.title == "V1Title"

    async def test_slug_uniqueness_after_soft_delete(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Creating a new item with the same slug as a soft-deleted one should work."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        d = await _create_directory(db_session, "Dir")
        dir_id = d.id
        await db_session.commit()

        # Create and approve
        pr1 = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [
                {
                    "op": "create_material",
                    "directory_id": str(dir_id),
                    "title": "Unique",
                    "type": "document",
                }
            ],
            title="PR 1 Long Title",
        )

        # Revert (soft-deletes the material)
        resp = await client.post(
            f"/api/pull-requests/{pr1['id']}/revert",
            headers=_auth_headers(admin),
        )
        assert resp.status_code == 201

        # Create another material with the same title/slug — should not conflict
        await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [
                {
                    "op": "create_material",
                    "directory_id": str(dir_id),
                    "title": "Unique",
                    "type": "document",
                }
            ],
            title="PR 2 Long Title",
        )
        # If we got here without an error, slug uniqueness works correctly
        result = await db_session.execute(select(Material).where(Material.title == "Unique"))
        assert result.scalar_one_or_none() is not None

    async def test_revert_notifies_author(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Reverting a PR should notify the original author."""
        student = await _create_user(db_session, UserRole.STUDENT)
        admin = await _create_user(db_session, UserRole.BUREAU)
        await db_session.commit()

        pr_data = await _create_and_approve_pr(
            client,
            db_session,
            student,
            admin,
            [{"op": "create_directory", "name": "Notified"}],
        )

        with patch("app.services.pr.notify_user", new_callable=AsyncMock) as mock_notify:
            resp = await client.post(
                f"/api/pull-requests/{pr_data['id']}/revert",
                headers=_auth_headers(admin),
            )
            assert resp.status_code == 201

            mock_notify.assert_called_once()
            args = mock_notify.call_args
            assert args[1].get("link") or (
                len(args[0]) >= 4 and "/pull-requests/" in str(args[0][3])
            )
