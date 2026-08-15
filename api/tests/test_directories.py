import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.user import User, UserRole


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
    user: User,
    parent_id: uuid.UUID | None = None,
    name: str = "Dir",
    slug: str = "dir",
) -> Directory:
    directory = Directory(
        id=uuid.uuid4(),
        parent_id=parent_id,
        name=name,
        slug=slug,
        type="folder",
        created_by=user.id,
    )
    db.add(directory)
    await db.flush()
    return directory


async def _create_material(db: AsyncSession, directory, user) -> Material:
    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Mat",
        slug="mat",
        type="pdf",
        author_id=user.id,
    )
    db.add(material)
    await db.flush()
    return material


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def test_get_directory_by_id(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    await db_session.commit()

    response = await client.get(f"/api/directories/{directory.id}", headers=_auth_headers(user))
    assert response.status_code == 200
    assert response.json()["id"] == str(directory.id)
    assert response.json()["name"] == "Dir"


async def test_get_directory_children(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    parent = await _create_directory(db_session, user)
    child_dir = await _create_directory(
        db_session, user, parent_id=parent.id, slug="child-dir", name="Child Dir"
    )
    child_mat = await _create_material(db_session, parent, user)
    await db_session.commit()

    response = await client.get(
        f"/api/directories/{parent.id}/children", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["directories"]) == 1
    assert data["directories"][0]["id"] == str(child_dir.id)
    assert len(data["materials"]) == 1
    assert data["materials"][0]["id"] == str(child_mat.id)


async def test_get_directory_path(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    root = await _create_directory(db_session, user, name="Root", slug="root")
    sub = await _create_directory(db_session, user, parent_id=root.id, name="Sub", slug="sub")
    await db_session.commit()

    response = await client.get(f"/api/directories/{sub.id}/path", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["id"] == str(root.id)
    assert data[1]["id"] == str(sub.id)


async def test_resolve_paths_batches_multiple_directory_paths(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    root = await _create_directory(db_session, user, name="Root", slug="root-batch")
    child = await _create_directory(
        db_session, user, parent_id=root.id, name="Child", slug="child-batch"
    )
    sibling = await _create_directory(
        db_session, user, parent_id=root.id, name="Sibling", slug="sibling-batch"
    )
    await db_session.commit()

    response = await client.post(
        "/api/directories/resolve-paths",
        json={"directory_ids": [str(child.id), str(sibling.id)], "material_ids": []},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    paths = response.json()["directories"]
    assert [item["id"] for item in paths[str(child.id)]] == [str(root.id), str(child.id)]
    assert [item["id"] for item in paths[str(sibling.id)]] == [str(root.id), str(sibling.id)]


async def test_resolve_paths_rejects_unbounded_identifier_fanout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    ids = [str(uuid.uuid4()) for _ in range(251)]

    response = await client.post(
        "/api/directories/resolve-paths",
        json={"directory_ids": ids, "material_ids": []},
        headers=_auth_headers(user),
    )
    assert response.status_code == 422


async def test_resolve_paths_combined_budget_counts_both_namespaces(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    # This shape used to pass a set-union budget because almost every UUID is
    # duplicated across the two namespaces, despite representing 251 units of
    # request work before material->directory expansion.
    directory_ids = [uuid.uuid4() for _ in range(126)]
    material_ids = directory_ids[:125]

    response = await client.post(
        "/api/directories/resolve-paths",
        json={
            "directory_ids": [str(value) for value in directory_ids],
            "material_ids": [str(value) for value in material_ids],
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 422
