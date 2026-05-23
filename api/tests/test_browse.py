import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.directory import Directory, DirectoryFavourite, DirectoryLike, DirectoryType
from app.models.material import Material, MaterialFavourite, MaterialLike, MaterialVersion
from app.models.user import User, UserRole


def _auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def _create_user(db: AsyncSession) -> User:
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


async def _create_directory(
    db: AsyncSession,
    user: User,
    *,
    name: str = "Test Dir",
    slug: str = "test-dir",
    parent_id: uuid.UUID | None = None,
    dir_type: DirectoryType = DirectoryType.FOLDER,
    sort_order: int = 0,
    is_system: bool = False,
) -> Directory:
    directory = Directory(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        type=dir_type,
        parent_id=parent_id,
        sort_order=sort_order,
        is_system=is_system,
        created_by=user.id,
    )
    db.add(directory)
    await db.flush()
    return directory


async def _create_material(
    db: AsyncSession,
    directory: Directory,
    user: User,
    *,
    title: str = "Test Material",
    slug: str = "test-material",
    mat_type: str = "pdf",
    parent_material_id: uuid.UUID | None = None,
) -> Material:
    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title=title,
        slug=slug,
        type=mat_type,
        author_id=user.id,
        parent_material_id=parent_material_id,
    )
    db.add(material)
    await db.flush()
    return material


async def _create_version(
    db: AsyncSession,
    material: Material,
    *,
    version_number: int = 1,
    file_key: str | None = "uploads/test/file.pdf",
    file_name: str | None = "file.pdf",
    file_size: int | None = 1024,
) -> MaterialVersion:
    version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material.id,
        version_number=version_number,
        file_key=file_key,
        file_name=file_name,
        file_size=file_size,
        file_mime_type="application/pdf",
    )
    db.add(version)
    await db.flush()
    return version


async def test_browse_root_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.get("/api/browse")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "directory_listing"
    assert data["directory"] is None
    assert data["directories"] == []
    assert data["materials"] == []


async def test_browse_root_with_directories(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await _create_directory(db_session, user, name="Alpha", slug="alpha", sort_order=1)
    await _create_directory(db_session, user, name="Beta", slug="beta", sort_order=0)
    await db_session.commit()

    response = await client.get("/api/browse")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "directory_listing"
    assert len(data["directories"]) == 2
    assert data["directories"][0]["name"] == "Beta"
    assert data["directories"][1]["name"] == "Alpha"


async def test_browse_root_excludes_system_dirs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await _create_directory(db_session, user, name="Visible", slug="visible")
    await _create_directory(db_session, user, name="System", slug="system", is_system=True)
    await db_session.commit()

    response = await client.get("/api/browse")
    assert response.status_code == 200
    data = response.json()
    assert len(data["directories"]) == 1
    assert data["directories"][0]["name"] == "Visible"


async def test_browse_path_directory(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    parent = await _create_directory(db_session, user, name="Parent", slug="parent")
    child = await _create_directory(
        db_session, user, name="Child", slug="child", parent_id=parent.id
    )
    await _create_material(db_session, child, user, title="Note", slug="note")
    await db_session.commit()

    response = await client.get("/api/browse/parent/child")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "directory_listing"
    assert data["directory"]["name"] == "Child"
    assert len(data["materials"]) == 1
    assert data["materials"][0]["title"] == "Note"


async def test_browse_path_material(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    dir_ = await _create_directory(db_session, user, name="Cours", slug="cours")
    material = await _create_material(db_session, dir_, user, title="Lecture", slug="lecture")
    await _create_version(db_session, material)
    await db_session.commit()

    response = await client.get("/api/browse/cours/lecture")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "material"
    assert data["material"]["title"] == "Lecture"
    assert data["material"]["current_version_info"] is not None


async def test_browse_path_material_no_version(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    dir_ = await _create_directory(db_session, user, name="Cours", slug="cours")
    await _create_material(db_session, dir_, user, title="Draft", slug="draft")
    await db_session.commit()

    response = await client.get("/api/browse/cours/draft")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "material"
    assert data["material"]["current_version_info"] is None


async def test_browse_path_attachments(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    dir_ = await _create_directory(db_session, user, name="Cours", slug="cours")
    parent_mat = await _create_material(db_session, dir_, user, title="Main", slug="main")
    await _create_material(
        db_session, dir_, user, title="Annex", slug="annex", parent_material_id=parent_mat.id
    )
    await db_session.commit()

    response = await client.get("/api/browse/cours/main/attachments")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "attachment_listing"
    assert len(data["materials"]) == 1
    assert data["materials"][0]["title"] == "Annex"


async def test_browse_path_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.get("/api/browse/nonexistent")
    assert response.status_code == 404


async def test_browse_path_nested_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await _create_directory(db_session, user, name="Existing", slug="existing")
    await db_session.commit()

    response = await client.get("/api/browse/existing/missing")
    assert response.status_code == 404


async def test_get_directory_by_id(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user, name="Course", slug="course")
    await db_session.commit()

    response = await client.get(f"/api/directories/{directory.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Course"
    assert data["slug"] == "course"


async def test_get_directory_not_found(client: AsyncClient) -> None:
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/directories/{fake_id}")
    assert response.status_code == 404


async def test_get_directory_children(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    parent = await _create_directory(db_session, user, name="Root", slug="root")
    await _create_directory(db_session, user, name="Sub A", slug="sub-a", parent_id=parent.id)
    await _create_material(db_session, parent, user, title="File", slug="file")
    await db_session.commit()

    response = await client.get(f"/api/directories/{parent.id}/children")
    assert response.status_code == 200
    data = response.json()
    assert len(data["directories"]) == 1
    assert data["directories"][0]["name"] == "Sub A"
    assert len(data["materials"]) == 1
    assert data["materials"][0]["title"] == "File"


async def test_get_directory_children_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user, name="Empty", slug="empty")
    await db_session.commit()

    response = await client.get(f"/api/directories/{directory.id}/children")
    assert response.status_code == 200
    data = response.json()
    assert data["directories"] == []
    assert data["materials"] == []


async def test_get_directory_path(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    root = await _create_directory(db_session, user, name="Root", slug="root")
    child = await _create_directory(db_session, user, name="Child", slug="child", parent_id=root.id)
    grandchild = await _create_directory(
        db_session, user, name="Grandchild", slug="grandchild", parent_id=child.id
    )
    await db_session.commit()

    response = await client.get(f"/api/directories/{grandchild.id}/path")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["name"] == "Root"
    assert data[1]["name"] == "Child"
    assert data[2]["name"] == "Grandchild"


async def test_browse_root_shows_child_counts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    parent = await _create_directory(db_session, user, name="Root", slug="root")
    await _create_directory(db_session, user, name="Sub", slug="sub", parent_id=parent.id)
    await _create_material(db_session, parent, user, title="M1", slug="m1")
    await _create_material(db_session, parent, user, title="M2", slug="m2")
    await db_session.commit()

    response = await client.get("/api/browse")
    assert response.status_code == 200
    data = response.json()
    root_dir = data["directories"][0]
    assert root_dir["child_directory_count"] == 1
    assert root_dir["child_material_count"] == 2


async def test_browse_deep_path_resolution_and_breadcrumbs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    a = await _create_directory(db_session, user, name="A", slug="a")
    b = await _create_directory(db_session, user, name="B", slug="b", parent_id=a.id)
    c = await _create_directory(db_session, user, name="C", slug="c", parent_id=b.id)
    d = await _create_directory(db_session, user, name="D", slug="d", parent_id=c.id)
    await _create_material(db_session, d, user, title="Deep", slug="deep")
    await db_session.commit()

    response = await client.get("/api/browse/a/b/c/d")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "directory_listing"
    assert data["directory"]["name"] == "D"
    assert data["directory"]["full_path"] == "a/b/c/d"
    # Breadcrumbs are reused from the single resolved path (F2).
    assert [bc["slug"] for bc in data["breadcrumbs"]] == ["a", "b", "c", "d"]
    assert len(data["materials"]) == 1
    assert data["materials"][0]["title"] == "Deep"


async def test_browse_same_slug_under_different_parents(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The batched directory walk must follow parent links, not just slugs."""
    user = await _create_user(db_session)
    p1 = await _create_directory(db_session, user, name="P1", slug="p1")
    p2 = await _create_directory(db_session, user, name="P2", slug="p2")
    shared1 = await _create_directory(
        db_session, user, name="Shared One", slug="shared", parent_id=p1.id
    )
    shared2 = await _create_directory(
        db_session, user, name="Shared Two", slug="shared", parent_id=p2.id
    )
    await _create_material(db_session, shared1, user, title="In One", slug="in-one")
    await _create_material(db_session, shared2, user, title="In Two", slug="in-two")
    await db_session.commit()

    resp1 = await client.get("/api/browse/p1/shared")
    resp2 = await client.get("/api/browse/p2/shared")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    data1 = resp1.json()
    data2 = resp2.json()
    assert data1["directory"]["id"] == str(shared1.id)
    assert data1["materials"][0]["title"] == "In One"
    assert data2["directory"]["id"] == str(shared2.id)
    assert data2["materials"][0]["title"] == "In Two"


async def test_browse_listing_reflects_user_likes_and_favourites(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    parent = await _create_directory(db_session, user, name="Parent", slug="parent")
    liked_dir = await _create_directory(
        db_session, user, name="Liked Dir", slug="liked-dir", parent_id=parent.id
    )
    await _create_directory(
        db_session, user, name="Plain Dir", slug="plain-dir", parent_id=parent.id
    )
    liked_mat = await _create_material(
        db_session, parent, user, title="Liked Mat", slug="liked-mat"
    )
    await _create_material(db_session, parent, user, title="Plain Mat", slug="plain-mat")

    db_session.add(DirectoryLike(id=uuid.uuid4(), user_id=user.id, directory_id=liked_dir.id))
    db_session.add(MaterialLike(id=uuid.uuid4(), user_id=user.id, material_id=liked_mat.id))
    db_session.add(MaterialFavourite(id=uuid.uuid4(), user_id=user.id, material_id=liked_mat.id))
    await db_session.commit()

    response = await client.get("/api/browse/parent", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()

    dirs = {d["name"]: d for d in data["directories"]}
    mats = {m["title"]: m for m in data["materials"]}
    assert dirs["Liked Dir"]["is_liked"] is True
    assert dirs["Plain Dir"]["is_liked"] is False
    assert mats["Liked Mat"]["is_liked"] is True
    assert mats["Liked Mat"]["is_favourited"] is True
    assert mats["Plain Mat"]["is_liked"] is False
    assert mats["Plain Mat"]["is_favourited"] is False


async def test_browse_listing_likes_isolated_per_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Another user's like must not surface as the current user's like."""
    owner = await _create_user(db_session)
    other = await _create_user(db_session)
    parent = await _create_directory(db_session, owner, name="Parent", slug="parent")
    mat = await _create_material(db_session, parent, owner, title="Mat", slug="mat")
    db_session.add(MaterialLike(id=uuid.uuid4(), user_id=other.id, material_id=mat.id))
    await db_session.commit()

    response = await client.get("/api/browse/parent", headers=_auth_headers(owner))
    assert response.status_code == 200
    data = response.json()
    assert data["materials"][0]["is_liked"] is False


async def test_browse_listing_anonymous_has_no_likes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    parent = await _create_directory(db_session, user, name="Parent", slug="parent")
    mat = await _create_material(db_session, parent, user, title="Mat", slug="mat")
    db_session.add(MaterialLike(id=uuid.uuid4(), user_id=user.id, material_id=mat.id))
    await db_session.commit()

    response = await client.get("/api/browse/parent")
    assert response.status_code == 200
    data = response.json()
    assert data["materials"][0]["is_liked"] is False
    assert data["materials"][0]["is_favourited"] is False


async def test_attachment_listing_has_versions_and_parent_like(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    dir_ = await _create_directory(db_session, user, name="Cours", slug="cours")
    parent_mat = await _create_material(db_session, dir_, user, title="Main", slug="main")
    annex = await _create_material(
        db_session, dir_, user, title="Annex", slug="annex", parent_material_id=parent_mat.id
    )
    await _create_version(db_session, annex)
    db_session.add(MaterialLike(id=uuid.uuid4(), user_id=user.id, material_id=parent_mat.id))
    db_session.add(DirectoryFavourite(id=uuid.uuid4(), user_id=user.id, directory_id=dir_.id))
    await db_session.commit()

    response = await client.get("/api/browse/cours/main/attachments", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "attachment_listing"
    assert len(data["materials"]) == 1
    # Version info is now batched in (previously an N+1 per-attachment query).
    assert data["materials"][0]["current_version_info"] is not None
    assert data["parent_material"]["is_liked"] is True
