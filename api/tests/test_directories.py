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
