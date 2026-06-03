import pytest

from app.models.directory import Directory, DirectoryType
from app.models.material import Material, MaterialVersion
from app.services.directory import get_preview_material_ids


@pytest.mark.asyncio
async def test_preview_backpropagates_from_subfolders(db_session):
    db = db_session
    course = Directory(name="Course", slug="course", type=DirectoryType.MODULE)
    db.add(course)
    await db.flush()
    ch1 = Directory(name="Ch1", slug="ch1", type=DirectoryType.FOLDER, parent_id=course.id)
    ch2 = Directory(name="Ch2", slug="ch2", type=DirectoryType.FOLDER, parent_id=course.id)
    db.add_all([ch1, ch2])
    await db.flush()

    by_id = {}
    for d, title in [(ch1, "A"), (ch1, "B"), (ch2, "C")]:
        m = Material(title=title, slug=title.lower(), type="document",
                     directory_id=d.id, current_version=1)
        db.add(m)
        await db.flush()
        db.add(MaterialVersion(material_id=m.id, version_number=1))
        by_id[str(m.id)] = title

    # a direct material on the course should win (depth 0)
    direct = Material(title="Zdirect", slug="zdirect", type="document",
                      directory_id=course.id, current_version=1)
    db.add(direct)
    await db.flush()
    db.add(MaterialVersion(material_id=direct.id, version_number=1))
    by_id[str(direct.id)] = "Zdirect"
    await db.commit()

    res = await get_preview_material_ids(db, [course.id])
    names = [by_id[i] for i in res[course.id]]
    # the course's own material (depth 0) comes first, then descendants
    assert names[0] == "Zdirect"
    assert set(names) == {"Zdirect", "A", "B", "C"}

    # ch1 (leaf folder) keeps its own direct materials
    res_ch1 = await get_preview_material_ids(db, [ch1.id])
    assert {by_id[i] for i in res_ch1[ch1.id]} == {"A", "B"}
