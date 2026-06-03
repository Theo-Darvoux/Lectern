import uuid

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.tag import Tag, directory_tags, material_tags
from app.services.tag import get_or_create_tags, prune_orphan_tags


async def test_get_or_create_tags_empty_list(db_session: AsyncSession) -> None:
    result = await get_or_create_tags(db_session, [])
    assert result == []


async def test_get_or_create_tags_whitespace_only(db_session: AsyncSession) -> None:
    result = await get_or_create_tags(db_session, [" ", "\t", ""])
    assert result == []


async def test_get_or_create_tags_normalizes_case_and_strips(db_session: AsyncSession) -> None:
    ids = await get_or_create_tags(db_session, ["Python", " python ", "PYTHON"])
    assert len(ids) == 1
    tags = (await db_session.execute(select(Tag).where(Tag.name == "python"))).scalars().all()
    assert len(tags) == 1


async def test_get_or_create_tags_creates_new(db_session: AsyncSession) -> None:
    ids = await get_or_create_tags(db_session, ["machine-learning"])
    assert len(ids) == 1
    tag = (await db_session.execute(select(Tag).where(Tag.id == ids[0]))).scalar_one()
    assert tag.name == "machine-learning"


async def test_get_or_create_tags_reuses_existing(db_session: AsyncSession) -> None:
    first = await get_or_create_tags(db_session, ["databases"])
    second = await get_or_create_tags(db_session, ["databases"])
    assert first == second
    all_tags = (
        (await db_session.execute(select(Tag).where(Tag.name == "databases"))).scalars().all()
    )
    assert len(all_tags) == 1


async def test_get_or_create_tags_mixed_new_and_existing(db_session: AsyncSession) -> None:
    await get_or_create_tags(db_session, ["existing"])
    ids = await get_or_create_tags(db_session, ["existing", "brand-new"])
    assert len(ids) == 2


async def test_prune_orphan_tags_removes_unreferenced(db_session: AsyncSession) -> None:
    tag = Tag(id=uuid.uuid4(), name="orphan-tag")
    db_session.add(tag)
    await db_session.flush()

    await prune_orphan_tags(db_session)

    result = (await db_session.execute(select(Tag).where(Tag.id == tag.id))).scalar_one_or_none()
    assert result is None


async def test_prune_orphan_tags_keeps_directory_referenced(db_session: AsyncSession) -> None:
    tag = Tag(id=uuid.uuid4(), name="in-use-tag")
    db_session.add(tag)
    directory = Directory(
        id=uuid.uuid4(),
        name="Course",
        slug=f"course-{uuid.uuid4().hex[:6]}",
        type="folder",
    )
    db_session.add(directory)
    await db_session.flush()

    await db_session.execute(
        insert(directory_tags).values(tag_id=tag.id, directory_id=directory.id)
    )
    await db_session.flush()

    await prune_orphan_tags(db_session)

    result = (await db_session.execute(select(Tag).where(Tag.id == tag.id))).scalar_one_or_none()
    assert result is not None


async def test_prune_orphan_tags_keeps_material_referenced(db_session: AsyncSession) -> None:
    tag = Tag(id=uuid.uuid4(), name="material-referenced-tag")
    db_session.add(tag)
    material = Material(
        id=uuid.uuid4(),
        title="Lecture Notes",
        slug=f"lecture-{uuid.uuid4().hex[:6]}",
        type="pdf",
    )
    db_session.add(material)
    await db_session.flush()

    await db_session.execute(insert(material_tags).values(tag_id=tag.id, material_id=material.id))
    await db_session.flush()

    await prune_orphan_tags(db_session)

    result = (await db_session.execute(select(Tag).where(Tag.id == tag.id))).scalar_one_or_none()
    assert result is not None


async def test_prune_orphan_tags_removes_only_orphans(db_session: AsyncSession) -> None:
    """A referenced tag survives while a co-existing orphan is pruned in the same call."""
    orphan = Tag(id=uuid.uuid4(), name="orphan")
    referenced = Tag(id=uuid.uuid4(), name="referenced")
    db_session.add_all([orphan, referenced])
    directory = Directory(
        id=uuid.uuid4(),
        name="Course",
        slug=f"course-{uuid.uuid4().hex[:6]}",
        type="folder",
    )
    db_session.add(directory)
    await db_session.flush()

    await db_session.execute(
        insert(directory_tags).values(tag_id=referenced.id, directory_id=directory.id)
    )
    await db_session.flush()

    await prune_orphan_tags(db_session)

    remaining = set((await db_session.execute(select(Tag.name))).scalars().all())
    assert "referenced" in remaining
    assert "orphan" not in remaining


async def test_prune_orphan_tags_noop_when_no_tags(db_session: AsyncSession) -> None:
    await prune_orphan_tags(db_session)
