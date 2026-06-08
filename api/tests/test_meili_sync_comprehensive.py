import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.services.pr import apply_pr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _user(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=UserRole.MODERATOR,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(u)
    await db.flush()
    return u


async def _directory(
    db: AsyncSession,
    name: str = "Dir",
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


async def _material(
    db: AsyncSession,
    title: str = "Mat",
    directory_id: uuid.UUID | None = None,
    author_id: uuid.UUID | None = None,
    parent_material_id: uuid.UUID | None = None,
) -> Material:
    m = Material(
        id=uuid.uuid4(),
        title=title,
        slug=title.lower().replace(" ", "-").replace("/", "-"),
        type="document",
        directory_id=directory_id,
        author_id=author_id,
        parent_material_id=parent_material_id,
        tags=[],
    )
    db.add(m)
    await db.flush()
    return m


async def _pr(db: AsyncSession, author: User, ops: list) -> PullRequest:
    pr = PullRequest(
        id=uuid.uuid4(),
        title="Comprehensive Sync PR",
        author_id=author.id,
        status=PRStatus.OPEN,
        payload=ops,
    )
    db.add(pr)
    await db.flush()
    return pr


def _deindex_jobs(db: AsyncSession) -> list[tuple]:
    """Extract de-indexing jobs from session info."""
    return [j for j in db.info.get("post_commit_jobs", []) if j[0] == "delete_indexed_item"]


def _index_jobs(db: AsyncSession) -> list[tuple]:
    """Extract indexing jobs from session info."""
    return [
        j
        for j in db.info.get("post_commit_jobs", [])
        if j[0] in ("index_material", "index_directory")
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_material_deletion_order_correctness(db_session: AsyncSession):
    """
    Ensures that material deletion enqueues a de-index job for the material.
    Critically, this verifies that the material is still "found" by the de-indexing
    logic before the hard delete occurs.
    """
    u = await _user(db_session)
    d = await _directory(db_session, "Courses", user_id=u.id)
    mat = await _material(db_session, "Algebra", directory_id=d.id, author_id=u.id)

    pr = await _pr(db_session, u, [{"op": "delete_material", "material_id": str(mat.id)}])

    # We apply the PR. If de-indexing happened after hard delete,
    # it might find 0 materials to de-index if the query logic is affected.
    await apply_pr(db_session, pr)

    jobs = _deindex_jobs(db_session)
    # Should have enqueued the material itself
    mat_deindex_ids = {j[2] for j in jobs if j[1] == "materials"}
    assert str(mat.id) in mat_deindex_ids


@pytest.mark.asyncio
async def test_recursive_attachment_deindexing(db_session: AsyncSession):
    """
    Verifies that deleting a material also de-indexes its recursive attachments
    (materials whose parent_material_id is the deleted material).
    """
    u = await _user(db_session)
    d = await _directory(db_session, "Root", user_id=u.id)

    parent = await _material(db_session, "Parent", directory_id=d.id, author_id=u.id)
    child = await _material(
        db_session, "Child Attachment", author_id=u.id, parent_material_id=parent.id
    )
    grandchild = await _material(
        db_session, "Grandchild", author_id=u.id, parent_material_id=child.id
    )

    pr = await _pr(db_session, u, [{"op": "delete_material", "material_id": str(parent.id)}])

    await apply_pr(db_session, pr)

    jobs = _deindex_jobs(db_session)
    mat_deindex_ids = {j[2] for j in jobs if j[1] == "materials"}

    assert str(parent.id) in mat_deindex_ids
    assert str(child.id) in mat_deindex_ids
    assert str(grandchild.id) in mat_deindex_ids


@pytest.mark.asyncio
async def test_directory_deletion_recursive_sync(db_session: AsyncSession):
    """
    Verifies that deleting a directory de-indexes all subdirectories and
    all materials within them, including their attachments.
    """
    u = await _user(db_session)
    root = await _directory(db_session, "Root", user_id=u.id)
    sub = await _directory(db_session, "Sub", parent_id=root.id, user_id=u.id)
    mat_in_sub = await _material(db_session, "MatInSub", directory_id=sub.id, author_id=u.id)
    att_of_mat = await _material(
        db_session, "Att", author_id=u.id, parent_material_id=mat_in_sub.id
    )

    pr = await _pr(db_session, u, [{"op": "delete_directory", "directory_id": str(root.id)}])

    await apply_pr(db_session, pr)

    jobs = _deindex_jobs(db_session)
    mat_deindex_ids = {j[2] for j in jobs if j[1] == "materials"}
    dir_deindex_ids = {j[2] for j in jobs if j[1] == "directories"}

    assert str(root.id) in dir_deindex_ids
    assert str(sub.id) in dir_deindex_ids
    assert str(mat_in_sub.id) in mat_deindex_ids
    assert str(att_of_mat.id) in mat_deindex_ids


@pytest.mark.asyncio
async def test_multi_op_pr_resilience(db_session: AsyncSession):
    """
    Tests a scenario where a single PR contains both a material deletion and
    a parent directory deletion. This verifies that already-deleted items in
    the session don't crash the subsequent de-indexing lookups.
    """
    u = await _user(db_session)
    root = await _directory(db_session, "Root", user_id=u.id)
    mat = await _material(db_session, "Mat", directory_id=root.id, author_id=u.id)

    # 1. Delete material (explicitly)
    # 2. Delete parent directory
    pr = await _pr(
        db_session,
        u,
        [
            {"op": "delete_material", "material_id": str(mat.id)},
            {"op": "delete_directory", "directory_id": str(root.id)},
        ],
    )

    await apply_pr(db_session, pr)

    jobs = _deindex_jobs(db_session)
    mat_deindex_ids = {j[2] for j in jobs if j[1] == "materials"}
    dir_deindex_ids = {j[2] for j in jobs if j[1] == "directories"}

    assert str(mat.id) in mat_deindex_ids
    assert str(root.id) in dir_deindex_ids


@pytest.mark.asyncio
async def test_indexing_on_create_edit(db_session: AsyncSession):
    """
    Verifies that creation and editing of materials/directories enqueues
    indexing jobs correctly.
    """
    u = await _user(db_session)

    # Create Dir
    pr_create_dir = await _pr(
        db_session, u, [{"op": "create_directory", "name": "NewDir", "type": "folder"}]
    )
    await apply_pr(db_session, pr_create_dir)

    # Resolve the new ID (it's random in create_directory but we can find it)
    new_dir = await db_session.scalar(select(Directory).where(Directory.name == "NewDir"))
    assert new_dir is not None

    jobs = _index_jobs(db_session)
    assert any(j[0] == "index_directory" and j[1] == new_dir.id for j in jobs)

    db_session.info["post_commit_jobs"] = []  # Clear
    db_session.info["post_commit_job_keys"] = set()  # Clear deduct keys

    # Create Mat in Dir
    pr_create_mat = await _pr(
        db_session,
        u,
        [
            {
                "op": "create_material",
                "title": "NewMat",
                "type": "document",
                "directory_id": str(new_dir.id),
            }
        ],
    )
    await apply_pr(db_session, pr_create_mat)
    new_mat = await db_session.scalar(select(Material).where(Material.title == "NewMat"))
    assert new_mat is not None

    jobs = _index_jobs(db_session)
    assert any(j[0] == "index_material" and j[1] == new_mat.id for j in jobs)

    db_session.info["post_commit_jobs"] = []  # Clear
    db_session.info["post_commit_job_keys"] = set()  # Clear deduct keys

    # Edit Dir
    pr_edit_dir = await _pr(
        db_session,
        u,
        [{"op": "edit_directory", "directory_id": str(new_dir.id), "description": "Updated"}],
    )
    await apply_pr(db_session, pr_edit_dir)
    jobs = _index_jobs(db_session)
    assert any(j[0] == "index_directory" and j[1] == new_dir.id for j in jobs)

    db_session.info["post_commit_jobs"] = []  # Clear
    db_session.info["post_commit_job_keys"] = set()  # Clear deduct keys

    # Edit Mat
    pr_edit_mat = await _pr(
        db_session,
        u,
        [{"op": "edit_material", "material_id": str(new_mat.id), "title": "UpdatedMat"}],
    )
    await apply_pr(db_session, pr_edit_mat)
    jobs = _index_jobs(db_session)
    assert any(j[0] == "index_material" and j[1] == new_mat.id for j in jobs)
