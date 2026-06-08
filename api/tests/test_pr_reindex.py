"""Tests for subtree reindex correctness in services/pr.py.

Covers:
- Directory rename → full subtree reindex enqueued
- Directory move → full subtree reindex enqueued
- Directory edit without name change → single reindex (not subtree)
- Material move → single reindex (no subtree)
- create_material / create_directory → correct single enqueue
- Deduplication — same ID enqueued only once per PR
- _enqueue_reindex_directory_recursive includes materials within descendants
"""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.services.pr import _enqueue_reindex_directory_recursive, apply_pr

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _user(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="T",
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
) -> Material:
    m = Material(
        id=uuid.uuid4(),
        title=title,
        slug=title.lower().replace(" ", "-"),
        type="document",
        directory_id=directory_id,
        author_id=author_id,
        tags=[],
    )
    db.add(m)
    await db.flush()
    return m


async def _pr(db: AsyncSession, author: User, ops: list) -> PullRequest:
    pr = PullRequest(
        id=uuid.uuid4(),
        title="Test PR",
        author_id=author.id,
        status=PRStatus.OPEN,
        payload=ops,
    )
    db.add(pr)
    await db.flush()
    return pr


def _index_jobs(db: AsyncSession) -> list[tuple]:
    return [
        j
        for j in db.info.get("post_commit_jobs", [])
        if j[0] in ("index_material", "index_directory")
    ]


def _deindex_jobs(db: AsyncSession) -> list[tuple]:
    return [j for j in db.info.get("post_commit_jobs", []) if j[0] == "delete_indexed_item"]


# ---------------------------------------------------------------------------
# _enqueue_reindex_directory_recursive (unit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_reindex_includes_self(db_session: AsyncSession):
    """Root dir with no children: only self is enqueued."""
    u = await _user(db_session)
    root = await _directory(db_session, "Root", user_id=u.id)
    db_session.info.setdefault("post_commit_jobs", [])

    await _enqueue_reindex_directory_recursive(db_session, root.id)

    jobs = _index_jobs(db_session)
    dir_ids = {j[1] for j in jobs if j[0] == "index_directory"}
    assert root.id in dir_ids


@pytest.mark.asyncio
async def test_enqueue_reindex_includes_descendants(db_session: AsyncSession):
    """All descendant dirs and their materials are enqueued."""
    u = await _user(db_session)
    root = await _directory(db_session, "Root", user_id=u.id)
    child = await _directory(db_session, "Child", parent_id=root.id, user_id=u.id)
    grandchild = await _directory(db_session, "Grand", parent_id=child.id, user_id=u.id)
    mat_root = await _material(db_session, "MatRoot", directory_id=root.id, author_id=u.id)
    mat_child = await _material(db_session, "MatChild", directory_id=child.id, author_id=u.id)
    db_session.info.setdefault("post_commit_jobs", [])

    await _enqueue_reindex_directory_recursive(db_session, root.id)

    jobs = _index_jobs(db_session)
    dir_ids = {j[1] for j in jobs if j[0] == "index_directory"}
    mat_ids = {j[1] for j in jobs if j[0] == "index_material"}

    assert root.id in dir_ids
    assert child.id in dir_ids
    assert grandchild.id in dir_ids
    assert mat_root.id in mat_ids
    assert mat_child.id in mat_ids


@pytest.mark.asyncio
async def test_enqueue_reindex_deduplication(db_session: AsyncSession):
    """Same ID is not enqueued twice even if called twice."""
    u = await _user(db_session)
    root = await _directory(db_session, "Root", user_id=u.id)
    db_session.info.setdefault("post_commit_jobs", [])

    await _enqueue_reindex_directory_recursive(db_session, root.id)
    await _enqueue_reindex_directory_recursive(db_session, root.id)  # second call

    dir_jobs = [
        j
        for j in db_session.info["post_commit_jobs"]
        if j[0] == "index_directory" and j[1] == root.id
    ]
    assert len(dir_jobs) == 1


# ---------------------------------------------------------------------------
# edit_directory — name change triggers subtree reindex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_directory_name_change_subtree_reindexed(db_session: AsyncSession):
    """Renaming a directory enqueues reindex for self + all descendants."""
    u = await _user(db_session)
    root = await _directory(db_session, "Old Name", user_id=u.id)
    child = await _directory(db_session, "Child", parent_id=root.id, user_id=u.id)
    mat = await _material(db_session, "Paper", directory_id=child.id, author_id=u.id)
    pr = await _pr(
        db_session, u, [{"op": "edit_directory", "directory_id": str(root.id), "name": "New Name"}]
    )

    with patch(
        "app.services.pr._enqueue_reindex_directory_recursive",
        wraps=_enqueue_reindex_directory_recursive,
    ) as spy:
        await apply_pr(db_session, pr)

    spy.assert_called_once_with(db_session, root.id)
    jobs = _index_jobs(db_session)
    dir_ids = {j[1] for j in jobs if j[0] == "index_directory"}
    mat_ids = {j[1] for j in jobs if j[0] == "index_material"}
    assert root.id in dir_ids
    assert child.id in dir_ids
    assert mat.id in mat_ids


@pytest.mark.asyncio
async def test_edit_directory_description_only_single_reindex(db_session: AsyncSession):
    """Editing description (no name/slug change) enqueues only self."""
    u = await _user(db_session)
    root = await _directory(db_session, "Root", user_id=u.id)
    child = await _directory(db_session, "Child", parent_id=root.id, user_id=u.id)
    pr = await _pr(
        db_session,
        u,
        [{"op": "edit_directory", "directory_id": str(root.id), "description": "New desc"}],
    )

    await apply_pr(db_session, pr)

    jobs = _index_jobs(db_session)
    dir_ids = [j[1] for j in jobs if j[0] == "index_directory"]
    # Only root — not child
    assert root.id in dir_ids
    assert child.id not in dir_ids


@pytest.mark.asyncio
async def test_edit_directory_tags_only_single_reindex(db_session: AsyncSession):
    """Editing tags (no name change) enqueues only self."""
    u = await _user(db_session)
    root = await _directory(db_session, "Root", user_id=u.id)
    child = await _directory(db_session, "Child", parent_id=root.id, user_id=u.id)
    pr = await _pr(
        db_session, u, [{"op": "edit_directory", "directory_id": str(root.id), "tags": []}]
    )

    await apply_pr(db_session, pr)

    jobs = _index_jobs(db_session)
    dir_ids = [j[1] for j in jobs if j[0] == "index_directory"]
    assert root.id in dir_ids
    assert child.id not in dir_ids


# ---------------------------------------------------------------------------
# move_item directory — triggers subtree reindex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_directory_subtree_reindexed(db_session: AsyncSession):
    """Moving a directory enqueues reindex for self + all descendants."""
    u = await _user(db_session)
    src = await _directory(db_session, "Src", user_id=u.id)
    child = await _directory(db_session, "Child", parent_id=src.id, user_id=u.id)
    mat = await _material(db_session, "Paper", directory_id=src.id, author_id=u.id)
    dest = await _directory(db_session, "Dest", user_id=u.id)
    pr = await _pr(
        db_session,
        u,
        [
            {
                "op": "move_item",
                "target_type": "directory",
                "target_id": str(src.id),
                "new_parent_id": str(dest.id),
            }
        ],
    )

    with patch(
        "app.services.pr._enqueue_reindex_directory_recursive",
        wraps=_enqueue_reindex_directory_recursive,
    ) as spy:
        await apply_pr(db_session, pr)

    spy.assert_called_once_with(db_session, src.id)
    jobs = _index_jobs(db_session)
    dir_ids = {j[1] for j in jobs if j[0] == "index_directory"}
    mat_ids = {j[1] for j in jobs if j[0] == "index_material"}
    assert src.id in dir_ids
    assert child.id in dir_ids
    assert mat.id in mat_ids


# ---------------------------------------------------------------------------
# move_item material — single reindex (no subtree)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_material_single_reindex(db_session: AsyncSession):
    """Moving a material only re-indexes that material."""
    u = await _user(db_session)
    src_dir = await _directory(db_session, "Src", user_id=u.id)
    dst_dir = await _directory(db_session, "Dst", user_id=u.id)
    mat = await _material(db_session, "Paper", directory_id=src_dir.id, author_id=u.id)
    pr = await _pr(
        db_session,
        u,
        [
            {
                "op": "move_item",
                "target_type": "material",
                "target_id": str(mat.id),
                "new_parent_id": str(dst_dir.id),
            }
        ],
    )

    await apply_pr(db_session, pr)

    jobs = _index_jobs(db_session)
    mat_ids = [j[1] for j in jobs if j[0] == "index_material"]
    assert mat.id in mat_ids
    # No directory reindex should be enqueued
    dir_ids = [j[1] for j in jobs if j[0] == "index_directory"]
    assert dir_ids == []


# ---------------------------------------------------------------------------
# create operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_material_enqueues_index(db_session: AsyncSession):
    u = await _user(db_session)
    d = await _directory(db_session, "Dir", user_id=u.id)
    pr = await _pr(
        db_session,
        u,
        [
            {
                "op": "create_material",
                "title": "New Mat",
                "type": "document",
                "directory_id": str(d.id),
            }
        ],
    )

    await apply_pr(db_session, pr)

    jobs = _index_jobs(db_session)
    assert any(j[0] == "index_material" for j in jobs)


@pytest.mark.asyncio
async def test_create_directory_enqueues_index(db_session: AsyncSession):
    u = await _user(db_session)
    pr = await _pr(
        db_session,
        u,
        [{"op": "create_directory", "name": "New Dir", "type": "folder"}],
    )

    await apply_pr(db_session, pr)

    jobs = _index_jobs(db_session)
    assert any(j[0] == "index_directory" for j in jobs)


# ---------------------------------------------------------------------------
# delete operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_material_enqueues_deindex(db_session: AsyncSession):
    u = await _user(db_session)
    d = await _directory(db_session, "Dir", user_id=u.id)
    mat = await _material(db_session, "Paper", directory_id=d.id, author_id=u.id)
    pr = await _pr(
        db_session,
        u,
        [{"op": "delete_material", "material_id": str(mat.id)}],
    )

    await apply_pr(db_session, pr)

    jobs = _deindex_jobs(db_session)
    item_ids = {j[2] for j in jobs}
    assert str(mat.id) in item_ids


@pytest.mark.asyncio
async def test_delete_directory_recursive_deindex(db_session: AsyncSession):
    """Deleting a directory deindexes it and all descendants."""
    u = await _user(db_session)
    root = await _directory(db_session, "Root", user_id=u.id)
    child = await _directory(db_session, "Child", parent_id=root.id, user_id=u.id)
    mat = await _material(db_session, "Paper", directory_id=child.id, author_id=u.id)
    pr = await _pr(
        db_session,
        u,
        [{"op": "delete_directory", "directory_id": str(root.id)}],
    )

    await apply_pr(db_session, pr)

    jobs = _deindex_jobs(db_session)
    item_ids = {j[2] for j in jobs}
    assert str(root.id) in item_ids
    assert str(child.id) in item_ids
    assert str(mat.id) in item_ids


# ---------------------------------------------------------------------------
# O(1) dedup across PR operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_same_directory_not_double_enqueued(db_session: AsyncSession):
    """If two ops would both enqueue the same directory, it appears only once."""
    u = await _user(db_session)
    d = await _directory(db_session, "Dir", user_id=u.id)
    # Two separate edits on the same directory (description changes — single enqueue each)
    pr = await _pr(
        db_session,
        u,
        [
            {"op": "edit_directory", "directory_id": str(d.id), "description": "First"},
            {"op": "edit_directory", "directory_id": str(d.id), "description": "Second"},
        ],
    )

    await apply_pr(db_session, pr)

    dir_jobs = [
        j for j in db_session.info["post_commit_jobs"] if j[0] == "index_directory" and j[1] == d.id
    ]
    assert len(dir_jobs) == 1
