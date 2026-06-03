"""Tests for SSE broadcast enqueuing on material/directory creation and deletion."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.services.pr import (
    _exec_create_directory,
    _exec_create_material,
    _soft_delete_directory_tree,
    _soft_delete_material_tree,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@telecom-sudparis.eu",
        display_name="Tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_directory(
    db: AsyncSession,
    user: User,
    *,
    name: str = "TestDir",
    parent_id: uuid.UUID | None = None,
) -> Directory:
    d = Directory(
        id=uuid.uuid4(),
        name=name,
        slug=name.lower().replace(" ", "-"),
        type="folder",
        parent_id=parent_id,
        created_by=user.id,
    )
    db.add(d)
    await db.flush()
    return d


async def _make_material(
    db: AsyncSession,
    user: User,
    directory: Directory | None,
    *,
    title: str = "TestMat",
    parent_material_id: uuid.UUID | None = None,
) -> Material:
    m = Material(
        id=uuid.uuid4(),
        directory_id=directory.id if directory else None,
        title=title,
        slug=title.lower().replace(" ", "-"),
        type="document",
        author_id=user.id,
        parent_material_id=parent_material_id,
    )
    db.add(m)
    await db.flush()
    return m


async def _make_pr(db: AsyncSession, user: User) -> PullRequest:
    pr = PullRequest(
        id=uuid.uuid4(),
        title="Test PR",
        payload=[],
        status=PRStatus.OPEN,
        author_id=user.id,
    )
    db.add(pr)
    await db.flush()
    return pr


def _broadcasts(db: AsyncSession) -> list[tuple[str, dict]]:
    return db.info.get("post_commit_sse_broadcasts", [])


# ---------------------------------------------------------------------------
# Deletion: material
# ---------------------------------------------------------------------------


class TestSoftDeleteMaterialBroadcasts:
    async def test_broadcasts_material_deleted_for_material(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session)
        directory = await _make_directory(db_session, user)
        mat = await _make_material(db_session, user, directory)
        db_session.info["post_commit_sse_broadcasts"] = []

        await _soft_delete_material_tree(db_session, mat)

        broadcasts = _broadcasts(db_session)
        topics = [b[0] for b in broadcasts]
        types = [b[1]["type"] for b in broadcasts]

        assert str(mat.id) in topics
        assert "material_deleted" in types

    async def test_broadcasts_material_deleted_for_attachment(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        directory = await _make_directory(db_session, user)
        parent_mat = await _make_material(db_session, user, directory, title="ParentMat")

        # Attachments are linked via parent_material_id; directory_id is None.
        att_mat = await _make_material(
            db_session,
            user,
            None,
            title="Attachment",
            parent_material_id=parent_mat.id,
        )
        db_session.info["post_commit_sse_broadcasts"] = []

        await _soft_delete_material_tree(db_session, parent_mat)

        broadcasts = _broadcasts(db_session)
        topics = {b[0] for b in broadcasts}

        assert str(parent_mat.id) in topics
        assert str(att_mat.id) in topics

        deleted_broadcasts = [b for b in broadcasts if b[1]["type"] == "material_deleted"]
        assert len(deleted_broadcasts) == 2

    async def test_no_attachment_broadcasts_without_sys_dir(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session)
        directory = await _make_directory(db_session, user)
        mat = await _make_material(db_session, user, directory)
        db_session.info["post_commit_sse_broadcasts"] = []

        await _soft_delete_material_tree(db_session, mat)

        broadcasts = _broadcasts(db_session)
        # The material itself is deleted, and its removal is broadcast to the parent directory
        assert len(broadcasts) == 2
        assert (str(mat.id), {"type": "material_deleted"}) in broadcasts
        assert (
            str(directory.id),
            {"type": "child_removed", "kind": "material", "id": str(mat.id)},
        ) in broadcasts


# ---------------------------------------------------------------------------
# Deletion: directory
# ---------------------------------------------------------------------------


class TestSoftDeleteDirectoryBroadcasts:
    async def test_broadcasts_directory_deleted_for_single_directory(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        directory = await _make_directory(db_session, user)
        db_session.info["post_commit_sse_broadcasts"] = []

        await _soft_delete_directory_tree(db_session, directory.id)

        broadcasts = _broadcasts(db_session)
        dir_broadcasts = [b for b in broadcasts if b[1]["type"] == "directory_deleted"]
        topics = {b[0] for b in dir_broadcasts}

        assert str(directory.id) in topics

    async def test_broadcasts_directory_deleted_for_whole_subtree(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        root = await _make_directory(db_session, user, name="Root")
        child = await _make_directory(db_session, user, name="Child", parent_id=root.id)
        grandchild = await _make_directory(db_session, user, name="GrandChild", parent_id=child.id)
        db_session.info["post_commit_sse_broadcasts"] = []

        await _soft_delete_directory_tree(db_session, root.id)

        broadcasts = _broadcasts(db_session)
        dir_broadcasts = [b for b in broadcasts if b[1]["type"] == "directory_deleted"]
        topics = {b[0] for b in dir_broadcasts}

        assert str(root.id) in topics
        assert str(child.id) in topics
        assert str(grandchild.id) in topics

    async def test_broadcasts_material_deleted_for_materials_in_tree(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        directory = await _make_directory(db_session, user)
        mat = await _make_material(db_session, user, directory)
        db_session.info["post_commit_sse_broadcasts"] = []

        await _soft_delete_directory_tree(db_session, directory.id)

        broadcasts = _broadcasts(db_session)
        mat_broadcasts = [b for b in broadcasts if b[1]["type"] == "material_deleted"]
        topics = {b[0] for b in mat_broadcasts}

        assert str(mat.id) in topics


# ---------------------------------------------------------------------------
# Creation: material
# ---------------------------------------------------------------------------


class TestCreateMaterialBroadcasts:
    async def test_child_added_broadcast_to_parent_directory(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        directory = await _make_directory(db_session, user)
        pr = await _make_pr(db_session, user)
        db_session.info["post_commit_sse_broadcasts"] = []

        payload = {
            "title": "New Material",
            "type": "document",
            "directory_id": str(directory.id),
        }
        await _exec_create_material(db_session, payload, pr, {})

        broadcasts = _broadcasts(db_session)
        child_broadcasts = [b for b in broadcasts if b[1].get("type") == "child_added"]
        assert len(child_broadcasts) == 1
        assert child_broadcasts[0][0] == str(directory.id)
        assert child_broadcasts[0][1]["kind"] == "material"

    async def test_child_added_broadcast_to_root_when_no_directory(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        pr = await _make_pr(db_session, user)
        db_session.info["post_commit_sse_broadcasts"] = []

        payload = {
            "title": "Root Material",
            "type": "document",
        }
        await _exec_create_material(db_session, payload, pr, {})

        broadcasts = _broadcasts(db_session)
        child_broadcasts = [b for b in broadcasts if b[1].get("type") == "child_added"]
        assert len(child_broadcasts) == 1
        assert child_broadcasts[0][0] == "root"
        assert child_broadcasts[0][1]["kind"] == "material"

    async def test_child_added_broadcast_contains_new_material_id(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        directory = await _make_directory(db_session, user)
        pr = await _make_pr(db_session, user)
        db_session.info["post_commit_sse_broadcasts"] = []

        payload = {
            "title": "Identified Material",
            "type": "document",
            "directory_id": str(directory.id),
        }
        new_id = await _exec_create_material(db_session, payload, pr, {})

        broadcasts = _broadcasts(db_session)
        child_broadcasts = [b for b in broadcasts if b[1].get("type") == "child_added"]
        assert child_broadcasts[0][1]["id"] == str(new_id)


# ---------------------------------------------------------------------------
# Creation: directory
# ---------------------------------------------------------------------------


class TestCreateDirectoryBroadcasts:
    async def test_child_added_broadcast_to_parent_directory(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        parent = await _make_directory(db_session, user, name="Parent")
        pr = await _make_pr(db_session, user)
        db_session.info["post_commit_sse_broadcasts"] = []

        payload = {"name": "Child Dir", "parent_id": str(parent.id)}
        await _exec_create_directory(db_session, payload, pr, {})

        broadcasts = _broadcasts(db_session)
        child_broadcasts = [b for b in broadcasts if b[1].get("type") == "child_added"]
        assert len(child_broadcasts) == 1
        assert child_broadcasts[0][0] == str(parent.id)
        assert child_broadcasts[0][1]["kind"] == "directory"

    async def test_child_added_broadcast_to_root_when_no_parent(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        pr = await _make_pr(db_session, user)
        db_session.info["post_commit_sse_broadcasts"] = []

        payload = {"name": "Top Level Dir"}
        await _exec_create_directory(db_session, payload, pr, {})

        broadcasts = _broadcasts(db_session)
        child_broadcasts = [b for b in broadcasts if b[1].get("type") == "child_added"]
        assert len(child_broadcasts) == 1
        assert child_broadcasts[0][0] == "root"
        assert child_broadcasts[0][1]["kind"] == "directory"

    async def test_child_added_broadcast_contains_new_directory_id(
        self, db_session: AsyncSession
    ) -> None:
        user = await _make_user(db_session)
        pr = await _make_pr(db_session, user)
        db_session.info["post_commit_sse_broadcasts"] = []

        payload = {"name": "Identified Dir"}
        new_id = await _exec_create_directory(db_session, payload, pr, {})

        broadcasts = _broadcasts(db_session)
        child_broadcasts = [b for b in broadcasts if b[1].get("type") == "child_added"]
        assert child_broadcasts[0][1]["id"] == str(new_id)
