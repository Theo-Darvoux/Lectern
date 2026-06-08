import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
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


async def _create_directory(db: AsyncSession, user: User) -> Directory:
    directory = Directory(
        id=uuid.uuid4(),
        name="Test Dir",
        slug="test-dir",
        type="folder",
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
    parent_material_id: uuid.UUID | None = None,
) -> Material:
    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title=title,
        slug=slug,
        type="pdf",
        author_id=user.id,
        parent_material_id=parent_material_id,
    )
    db.add(material)
    await db.flush()
    return material


async def _create_version(
    db: AsyncSession,
    material: Material,
    version_number: int = 1,
    *,
    file_key: str | None = "uploads/test/file.pdf",
    file_size: int | None = 2048,
) -> MaterialVersion:
    version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material.id,
        version_number=version_number,
        file_key=file_key,
        file_name="file.pdf",
        file_size=file_size,
        file_mime_type="application/pdf",
    )
    db.add(version)
    await db.flush()
    return version


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def test_get_material(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await _create_version(db_session, material)
    await db_session.commit()

    response = await client.get(f"/api/materials/{material.id}", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Material"
    assert data["current_version_info"] is not None
    assert data["current_version_info"]["version_number"] == 1


async def test_get_material_without_version(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await db_session.commit()

    response = await client.get(f"/api/materials/{material.id}", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Material"
    assert data["current_version_info"] is None


async def test_get_material_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/materials/{fake_id}", headers=_auth_headers(user))
    assert response.status_code == 404


async def test_list_versions(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await _create_version(db_session, material, 1)
    await _create_version(db_session, material, 2, file_size=4096)
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{material.id}/versions", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["version_number"] == 2
    assert data[1]["version_number"] == 1


async def test_list_versions_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{material.id}/versions", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_list_versions_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/materials/{fake_id}/versions", headers=_auth_headers(user))
    assert response.status_code == 404


async def test_get_specific_version(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await _create_version(db_session, material, 1)
    await _create_version(db_session, material, 2, file_size=8192)
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{material.id}/versions/2", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    data = response.json()
    assert data["version_number"] == 2
    assert data["file_size"] == 8192


async def test_get_specific_version_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{material.id}/versions/999", headers=_auth_headers(user)
    )
    assert response.status_code == 404


async def test_list_attachments(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    parent = await _create_material(db_session, directory, user, title="Main", slug="main")
    await _create_material(
        db_session,
        directory,
        user,
        title="Attachment A",
        slug="attach-a",
        parent_material_id=parent.id,
    )
    await _create_material(
        db_session,
        directory,
        user,
        title="Attachment B",
        slug="attach-b",
        parent_material_id=parent.id,
    )
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{parent.id}/attachments", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    titles = {a["title"] for a in data}
    assert titles == {"Attachment A", "Attachment B"}


async def test_list_attachments_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{material.id}/attachments", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_list_attachments_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()
    fake_id = str(uuid.uuid4())
    response = await client.get(
        f"/api/materials/{fake_id}/attachments", headers=_auth_headers(user)
    )
    assert response.status_code == 404


async def test_view_material(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await db_session.commit()

    response = await client.post(
        f"/api/materials/{material.id}/view",
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_view_material_twice_updates(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await db_session.commit()

    response1 = await client.post(
        f"/api/materials/{material.id}/view",
        headers=_auth_headers(user),
    )
    assert response1.status_code == 200

    response2 = await client.post(
        f"/api/materials/{material.id}/view",
        headers=_auth_headers(user),
    )
    assert response2.status_code == 200


async def test_view_material_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    fake_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/materials/{fake_id}/view",
        headers=_auth_headers(user),
    )
    assert response.status_code == 404


async def test_view_material_unauthenticated(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material = await _create_material(db_session, directory, user)
    await db_session.commit()

    response = await client.post(f"/api/materials/{material.id}/view")
    assert response.status_code == 401


async def test_material_detail_comment_and_annotation_counts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /materials/{id} returns correct comment_count and annotation_count.

    Plan H: expose activity counters on material detail so the frontend can show
    badges on the sidebar tabs without polling.
    """
    from app.models.annotation import Annotation
    from app.models.comment import Comment

    actor = await _create_user(db_session)
    directory = await _create_directory(db_session, actor)
    material = await _create_material(db_session, directory, actor)
    await db_session.commit()

    # --- zero counts initially ---
    r = await client.get(f"/api/materials/{material.id}", headers=_auth_headers(actor))
    assert r.status_code == 200
    data = r.json()
    assert data["comment_count"] == 0
    assert data["annotation_count"] == 0

    # --- add 2 comments and 3 annotations ---
    for _ in range(2):
        db_session.add(
            Comment(
                id=uuid.uuid4(),
                target_type="material",
                target_id=material.id,
                body="hi",
                author_id=actor.id,
            )
        )
    for _ in range(3):
        db_session.add(
            Annotation(
                id=uuid.uuid4(),
                material_id=material.id,
                author_id=actor.id,
                body="note",
                page=1,
            )
        )
    await db_session.commit()

    r2 = await client.get(f"/api/materials/{material.id}", headers=_auth_headers(actor))
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["comment_count"] == 2
    assert data2["annotation_count"] == 3
