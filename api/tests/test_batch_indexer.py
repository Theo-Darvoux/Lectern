"""Tests for batch indexing, get_ancestor_map, and post-commit job coalescing."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _user(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="T",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(u)
    await db.flush()
    return u


async def _dir(
    db: AsyncSession,
    name: str = "Dir",
    slug: str | None = None,
    parent_id: uuid.UUID | None = None,
) -> Directory:
    d = Directory(
        id=uuid.uuid4(),
        name=name,
        slug=slug or name.lower(),
        type="folder",
        parent_id=parent_id,
    )
    db.add(d)
    await db.flush()
    return d


async def _mat(
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


# ---------------------------------------------------------------------------
# get_ancestor_map
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ancestor_map_empty_input(db_session: AsyncSession):
    from app.services.directory import get_ancestor_map

    result = await get_ancestor_map(db_session, set())
    assert result == {}


@pytest.mark.asyncio
async def test_ancestor_map_root_directory(db_session: AsyncSession):
    """Root dir (no parent) → name_path = its own name, slug_path = its own slug."""
    root = await _dir(db_session, "Mathematics", "mathematics")

    from app.services.directory import get_ancestor_map

    result = await get_ancestor_map(db_session, {root.id})
    assert root.id in result
    name_path, slug_path = result[root.id]
    assert name_path == "Mathematics"
    assert slug_path == "mathematics"


@pytest.mark.asyncio
async def test_ancestor_map_one_level_deep(db_session: AsyncSession):
    """Child dir → paths include root then child."""
    root = await _dir(db_session, "Science", "science")
    child = await _dir(db_session, "Physics", "physics", parent_id=root.id)

    from app.services.directory import get_ancestor_map

    result = await get_ancestor_map(db_session, {child.id})
    assert child.id in result
    name_path, slug_path = result[child.id]
    assert name_path == "Science Physics"
    assert slug_path == "science/physics"


@pytest.mark.asyncio
async def test_ancestor_map_two_levels_deep(db_session: AsyncSession):
    """Grandchild dir → paths accumulated through root → child → grandchild."""
    root = await _dir(db_session, "Science", "science")
    child = await _dir(db_session, "Physics", "physics", parent_id=root.id)
    grand = await _dir(db_session, "Optics", "optics", parent_id=child.id)

    from app.services.directory import get_ancestor_map

    result = await get_ancestor_map(db_session, {grand.id})
    name_path, slug_path = result[grand.id]
    assert name_path == "Science Physics Optics"
    assert slug_path == "science/physics/optics"


@pytest.mark.asyncio
async def test_ancestor_map_multiple_directories_single_query(db_session: AsyncSession):
    """Multiple directory IDs resolved in a single call — all present in result."""
    root = await _dir(db_session, "Root", "root")
    child_a = await _dir(db_session, "A", "a", parent_id=root.id)
    child_b = await _dir(db_session, "B", "b", parent_id=root.id)

    from app.services.directory import get_ancestor_map

    result = await get_ancestor_map(db_session, {root.id, child_a.id, child_b.id})
    assert len(result) == 3
    assert result[child_a.id][1] == "root/a"
    assert result[child_b.id][1] == "root/b"


@pytest.mark.asyncio
async def test_ancestor_map_unknown_id_not_in_result(db_session: AsyncSession):
    """IDs not in the DB are silently absent from the result dict."""
    from app.services.directory import get_ancestor_map

    missing = uuid.uuid4()
    result = await get_ancestor_map(db_session, {missing})
    assert missing not in result


# ---------------------------------------------------------------------------
# index_materials_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_materials_batch_single_add_documents_call(db_session: AsyncSession):
    """Batch indexer issues exactly one add_documents call regardless of batch size."""
    u = await _user(db_session)
    parent = await _dir(db_session, "Course", "course")
    mats = [
        await _mat(db_session, f"Mat{i}", directory_id=parent.id, author_id=u.id) for i in range(5)
    ]
    await db_session.commit()

    mock_index = AsyncMock()
    mock_index.add_documents = AsyncMock()

    import app.core.database.database as c_db

    orig = c_db.async_session_factory

    # Patch the session factory used by the worker to use the test DB
    engine = db_session.bind

    def _make_test_factory():
        from sqlalchemy.ext.asyncio import async_sessionmaker as asm

        return asm(engine, expire_on_commit=False)

    test_factory = _make_test_factory()
    c_db.async_session_factory = test_factory

    try:
        with patch("app.workers.index_content.meili_admin_client") as mock_admin:
            mock_admin.index = MagicMock(return_value=mock_index)
            from app.workers.index_content import index_materials_batch

            await index_materials_batch({}, [m.id for m in mats])

        mock_index.add_documents.assert_called_once()
        docs = mock_index.add_documents.call_args[0][0]
        assert len(docs) == 5
        doc_ids = {d["id"] for d in docs}
        assert all(str(m.id) in doc_ids for m in mats)
    finally:
        c_db.async_session_factory = orig


@pytest.mark.asyncio
async def test_index_materials_batch_ancestor_path_correct(db_session: AsyncSession):
    """Batch indexer populates ancestor_path from get_ancestor_map."""
    u = await _user(db_session)
    root = await _dir(db_session, "Science", "science")
    child = await _dir(db_session, "Physics", "physics", parent_id=root.id)
    mat = await _mat(db_session, "Optics Paper", directory_id=child.id, author_id=u.id)
    await db_session.commit()

    import app.core.database.database as c_db

    orig = c_db.async_session_factory
    engine = db_session.bind
    from sqlalchemy.ext.asyncio import async_sessionmaker as asm

    c_db.async_session_factory = asm(engine, expire_on_commit=False)

    captured_docs = []

    try:
        with patch("app.workers.index_content.meili_admin_client") as mock_admin:
            mock_idx = AsyncMock()
            mock_idx.add_documents = AsyncMock(side_effect=lambda docs: captured_docs.extend(docs))
            mock_admin.index = MagicMock(return_value=mock_idx)
            from app.workers.index_content import index_materials_batch

            await index_materials_batch({}, [mat.id])
    finally:
        c_db.async_session_factory = orig

    assert len(captured_docs) == 1
    doc = captured_docs[0]
    assert doc["ancestor_path"] == "Science Physics"
    assert "/science/physics/" in doc["browse_path"]


@pytest.mark.asyncio
async def test_index_materials_batch_empty_list(db_session: AsyncSession):
    """Empty list → no DB query, no Meili call."""
    with patch("app.workers.index_content.meili_admin_client") as mock_admin:
        from app.workers.index_content import index_materials_batch

        await index_materials_batch({}, [])
        mock_admin.index.assert_not_called()


# ---------------------------------------------------------------------------
# index_directories_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_directories_batch_single_add_documents_call(db_session: AsyncSession):
    """Batch dir indexer issues exactly one add_documents call."""
    root = await _dir(db_session, "Root", "root")
    children = [
        await _dir(db_session, f"Child{i}", f"child-{i}", parent_id=root.id) for i in range(4)
    ]
    await db_session.commit()

    import app.core.database.database as c_db

    orig = c_db.async_session_factory
    engine = db_session.bind
    from sqlalchemy.ext.asyncio import async_sessionmaker as asm

    c_db.async_session_factory = asm(engine, expire_on_commit=False)

    mock_idx = AsyncMock()
    mock_idx.add_documents = AsyncMock()

    try:
        with patch("app.workers.index_content.meili_admin_client") as mock_admin:
            mock_admin.index = MagicMock(return_value=mock_idx)
            from app.workers.index_content import index_directories_batch

            await index_directories_batch({}, [c.id for c in children])

        mock_idx.add_documents.assert_called_once()
        docs = mock_idx.add_documents.call_args[0][0]
        assert len(docs) == 4
    finally:
        c_db.async_session_factory = orig


@pytest.mark.asyncio
async def test_index_directories_batch_ancestor_path_uses_parent(db_session: AsyncSession):
    """Directory batch indexer uses parent's path for ancestor_path, not its own."""
    root = await _dir(db_session, "Science", "science")
    child = await _dir(db_session, "Physics", "physics", parent_id=root.id)
    await db_session.commit()

    import app.core.database.database as c_db

    orig = c_db.async_session_factory
    engine = db_session.bind
    from sqlalchemy.ext.asyncio import async_sessionmaker as asm

    c_db.async_session_factory = asm(engine, expire_on_commit=False)

    captured = []

    try:
        with patch("app.workers.index_content.meili_admin_client") as mock_admin:
            mock_idx = AsyncMock()
            mock_idx.add_documents = AsyncMock(side_effect=lambda docs: captured.extend(docs))
            mock_admin.index = MagicMock(return_value=mock_idx)
            from app.workers.index_content import index_directories_batch

            await index_directories_batch({}, [child.id])
    finally:
        c_db.async_session_factory = orig

    assert len(captured) == 1
    doc = captured[0]
    # ancestor_path should be parent's name (Science), not child's own name
    assert doc["ancestor_path"] == "Science"
    assert doc["browse_path"].endswith("/physics")


@pytest.mark.asyncio
async def test_index_directories_batch_root_has_empty_ancestor_path(db_session: AsyncSession):
    """Root-level directory has empty ancestor_path."""
    root = await _dir(db_session, "Root", "root")
    await db_session.commit()

    import app.core.database.database as c_db

    orig = c_db.async_session_factory
    engine = db_session.bind
    from sqlalchemy.ext.asyncio import async_sessionmaker as asm

    c_db.async_session_factory = asm(engine, expire_on_commit=False)

    captured = []

    try:
        with patch("app.workers.index_content.meili_admin_client") as mock_admin:
            mock_idx = AsyncMock()
            mock_idx.add_documents = AsyncMock(side_effect=lambda docs: captured.extend(docs))
            mock_admin.index = MagicMock(return_value=mock_idx)
            from app.workers.index_content import index_directories_batch

            await index_directories_batch({}, [root.id])
    finally:
        c_db.async_session_factory = orig

    assert captured[0]["ancestor_path"] == ""
    assert captured[0]["browse_path"] == "/browse/root"


@pytest.mark.asyncio
async def test_index_directories_batch_empty_list():
    with patch("app.workers.index_content.meili_admin_client") as mock_admin:
        from app.workers.index_content import index_directories_batch

        await index_directories_batch({}, [])
        mock_admin.index.assert_not_called()


@pytest.mark.asyncio
async def test_delete_indexed_item_propagates_failure_for_worker_retry() -> None:
    from app.workers.index_content import delete_indexed_item

    index = MagicMock()
    index.delete_document = AsyncMock(side_effect=OSError("search unavailable"))
    with patch("app.workers.index_content.meili_admin_client.index", return_value=index):
        with pytest.raises(OSError, match="search unavailable"):
            await delete_indexed_item({}, "materials", "material-id")


# ---------------------------------------------------------------------------
# Post-commit job coalescing (_coalesce_index_jobs)
# ---------------------------------------------------------------------------


def test_coalesce_single_material():
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    mid = uuid.uuid4()
    result = _coalesce_index_jobs([("index_material", mid)])
    assert result == [("index_material", mid)]


def test_coalesce_two_consecutive_materials_become_batch():
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    m1, m2 = uuid.uuid4(), uuid.uuid4()
    result = _coalesce_index_jobs([("index_material", m1), ("index_material", m2)])
    assert result == [("index_materials_batch", [m1, m2])]


def test_coalesce_five_consecutive_materials():
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    ids = [uuid.uuid4() for _ in range(5)]
    jobs = [("index_material", i) for i in ids]
    result = _coalesce_index_jobs(jobs)
    assert len(result) == 1
    assert result[0][0] == "index_materials_batch"
    assert result[0][1] == ids


def test_coalesce_single_directory():
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    did = uuid.uuid4()
    result = _coalesce_index_jobs([("index_directory", did)])
    assert result == [("index_directory", did)]


def test_coalesce_two_consecutive_directories_become_batch():
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    d1, d2 = uuid.uuid4(), uuid.uuid4()
    result = _coalesce_index_jobs([("index_directory", d1), ("index_directory", d2)])
    assert result == [("index_directories_batch", [d1, d2])]


def test_coalesce_preserves_non_index_jobs():
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    del_job = ("delete_indexed_item", "materials", "abc")
    storage_job = ("delete_storage_objects", ["key1"])
    result = _coalesce_index_jobs([del_job, storage_job])
    assert result == [del_job, storage_job]


def test_coalesce_interleaved_preserves_order():
    """delete_indexed_item between two index runs → two separate batch calls."""
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    m1, m2, m3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    del_job = ("delete_indexed_item", "materials", "x")
    jobs = [
        ("index_material", m1),
        ("index_material", m2),
        del_job,
        ("index_material", m3),
    ]
    result = _coalesce_index_jobs(jobs)
    assert result[0] == ("index_materials_batch", [m1, m2])
    assert result[1] == del_job
    assert result[2] == ("index_material", m3)


def test_coalesce_mixed_material_then_directory():
    """Consecutive mats then consecutive dirs → two batches."""
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    m1, m2 = uuid.uuid4(), uuid.uuid4()
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    jobs = [
        ("index_material", m1),
        ("index_material", m2),
        ("index_directory", d1),
        ("index_directory", d2),
    ]
    result = _coalesce_index_jobs(jobs)
    assert result[0] == ("index_materials_batch", [m1, m2])
    assert result[1] == ("index_directories_batch", [d1, d2])


def test_coalesce_empty_list():
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    assert _coalesce_index_jobs([]) == []


def test_coalesce_complex_sequence():
    """Full realistic sequence from a subtree rename."""
    from app.core.events.coalesce import coalesce_index_jobs as _coalesce_index_jobs

    d1, d2, d3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    m1, m2 = uuid.uuid4(), uuid.uuid4()
    del_job = ("delete_indexed_item", "materials", "stale")

    jobs = [
        del_job,
        ("index_directory", d1),
        ("index_directory", d2),
        ("index_directory", d3),
        ("index_material", m1),
        ("index_material", m2),
    ]
    result = _coalesce_index_jobs(jobs)
    assert result[0] == del_job
    assert result[1] == ("index_directories_batch", [d1, d2, d3])
    assert result[2] == ("index_materials_batch", [m1, m2])


# ---------------------------------------------------------------------------
# split_identifiers
# ---------------------------------------------------------------------------


def test_split_identifiers_alphanumeric():
    from app.workers.index_content import split_identifiers

    assert split_identifiers("CS101") == "CS 101"
    assert split_identifiers("101CS") == "101 CS"
    assert split_identifiers("Math2A") == "Math 2 A"


def test_split_identifiers_empty():
    from app.workers.index_content import split_identifiers

    assert split_identifiers("") == ""


def test_split_identifiers_no_change():
    from app.workers.index_content import split_identifiers

    assert split_identifiers("algebra") == "algebra"
    assert split_identifiers("linear algebra") == "linear algebra"


def test_split_identifiers_module_scope():
    """Compiled patterns live at module scope — not re-created on each call."""
    import app.workers.index_content as mod

    assert hasattr(mod, "_ALPHA_NUM")
    assert hasattr(mod, "_NUM_ALPHA")
    import re

    assert isinstance(mod._ALPHA_NUM, type(re.compile("")))
