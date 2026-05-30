import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.annotation import Annotation
from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
from app.models.user import User, UserRole


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@telecom-sudparis.eu",
        display_name="Test User",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _create_material(db: AsyncSession, user: User) -> tuple[Material, MaterialVersion]:
    directory = Directory(
        id=uuid.uuid4(),
        name="Test Dir",
        slug="test-dir",
        type="folder",
        created_by=user.id,
    )
    db.add(directory)
    await db.flush()

    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Test Material",
        slug="test-material",
        type="document",
        current_version=1,
        author_id=user.id,
    )
    db.add(material)
    await db.flush()

    version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material.id,
        version_number=1,
        file_key="test/file.pdf",
        file_name="file.pdf",
        file_size=1024,
        file_mime_type="application/pdf",
    )
    db.add(version)
    await db.flush()

    return material, version


async def _create_annotation(
    db: AsyncSession, user: User, material: Material, version: MaterialVersion
) -> Annotation:
    annotation = Annotation(
        material_id=material.id,
        version_id=version.id,
        author_id=user.id,
        body="Test annotation",
        page=1,
        selection_text="selected text",
        position_data={"startOffset": 0, "endOffset": 10, "textContent": "selected text"},
    )
    db.add(annotation)
    await db.flush()
    annotation.thread_id = annotation.id
    await db.flush()
    return annotation


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def test_list_annotations_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material, _ = await _create_material(db_session, user)
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{material.id}/annotations",
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


async def test_create_root_annotation(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material, _ = await _create_material(db_session, user)
    await db_session.commit()

    response = await client.post(
        f"/api/materials/{material.id}/annotations",
        json={
            "body": "Great insight here!",
            "selection_text": "some text",
            "position_data": {"startOffset": 0, "endOffset": 5, "textContent": "some text"},
            "page": 1,
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["body"] == "Great insight here!"
    assert data["selection_text"] == "some text"
    assert data["page"] == 1
    assert data["material_id"] == str(material.id)
    assert data["author_id"] == str(user.id)
    assert data["thread_id"] == data["id"]
    assert data["reply_to_id"] is None


async def test_create_root_annotation_requires_position_data(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    material, _ = await _create_material(db_session, user)
    await db_session.commit()

    response = await client.post(
        f"/api/materials/{material.id}/annotations",
        json={
            "body": "No position data!",
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 400


async def test_create_reply_annotation(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material, version = await _create_material(db_session, user)
    root = await _create_annotation(db_session, user, material, version)
    await db_session.commit()

    response = await client.post(
        f"/api/materials/{material.id}/annotations",
        json={
            "body": "I agree!",
            "reply_to_id": str(root.id),
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["body"] == "I agree!"
    assert data["thread_id"] == str(root.id)
    assert data["reply_to_id"] == str(root.id)


async def test_create_reply_to_nonexistent(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material, _ = await _create_material(db_session, user)
    await db_session.commit()

    fake_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/materials/{material.id}/annotations",
        json={
            "body": "Reply to nothing",
            "reply_to_id": fake_id,
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 404


async def test_list_annotations_with_threads(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material, version = await _create_material(db_session, user)
    root = await _create_annotation(db_session, user, material, version)

    reply = Annotation(
        material_id=material.id,
        version_id=version.id,
        author_id=user.id,
        body="Reply body",
        thread_id=root.id,
        reply_to_id=root.id,
    )
    db_session.add(reply)
    await db_session.flush()
    await db_session.commit()

    response = await client.get(
        f"/api/materials/{material.id}/annotations",
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    thread = data["items"][0]
    assert thread["root"]["id"] == str(root.id)
    assert len(thread["replies"]) == 1
    assert thread["replies"][0]["body"] == "Reply body"


async def test_edit_annotation(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material, version = await _create_material(db_session, user)
    annotation = await _create_annotation(db_session, user, material, version)
    await db_session.commit()

    response = await client.patch(
        f"/api/annotations/{annotation.id}",
        json={"body": "Updated annotation"},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    assert response.json()["body"] == "Updated annotation"


async def test_edit_annotation_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    user1 = await _create_user(db_session)
    user2 = await _create_user(db_session)
    material, version = await _create_material(db_session, user1)
    annotation = await _create_annotation(db_session, user1, material, version)
    await db_session.commit()

    response = await client.patch(
        f"/api/annotations/{annotation.id}",
        json={"body": "Hacked"},
        headers=_auth_headers(user2),
    )
    assert response.status_code == 403


async def test_delete_annotation_author(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material, version = await _create_material(db_session, user)
    annotation = await _create_annotation(db_session, user, material, version)
    await db_session.commit()

    response = await client.delete(
        f"/api/annotations/{annotation.id}",
        headers=_auth_headers(user),
    )
    assert response.status_code == 204


async def test_delete_annotation_moderator(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _create_user(db_session)
    mod = await _create_user(db_session, role=UserRole.MODERATOR)
    material, version = await _create_material(db_session, student)
    annotation = await _create_annotation(db_session, student, material, version)
    await db_session.commit()

    response = await client.delete(
        f"/api/annotations/{annotation.id}",
        headers=_auth_headers(mod),
    )
    assert response.status_code == 204


async def test_delete_annotation_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    user1 = await _create_user(db_session)
    user2 = await _create_user(db_session)
    material, version = await _create_material(db_session, user1)
    annotation = await _create_annotation(db_session, user1, material, version)
    await db_session.commit()

    response = await client.delete(
        f"/api/annotations/{annotation.id}",
        headers=_auth_headers(user2),
    )
    assert response.status_code == 403


async def test_edit_nonexistent_annotation(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    fake_id = str(uuid.uuid4())
    response = await client.patch(
        f"/api/annotations/{fake_id}",
        json={"body": "Updated"},
        headers=_auth_headers(user),
    )
    assert response.status_code == 404


async def test_delete_nonexistent_annotation(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    fake_id = str(uuid.uuid4())
    response = await client.delete(
        f"/api/annotations/{fake_id}",
        headers=_auth_headers(user),
    )
    assert response.status_code == 404


async def test_annotations_on_nonexistent_material(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    fake_id = str(uuid.uuid4())
    response = await client.get(
        f"/api/materials/{fake_id}/annotations",
        headers=_auth_headers(user),
    )
    assert response.status_code == 404


async def test_create_annotation_on_nonexistent_material(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    fake_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/materials/{fake_id}/annotations",
        json={
            "body": "Orphan annotation",
            "position_data": {"startOffset": 0, "endOffset": 5},
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 404


async def test_annotations_and_comments_survive_pdf_replacement(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Replacing a PDF (new MaterialVersion + current_version bump) must NOT drop
    annotations or comments. Both are keyed on the stable material id, not on the
    file/version, so they stay visible after a replacement.

    Locks the current behaviour so a future upload/PR refactor cannot silently
    lose user annotations and chats.
    """
    user = await _create_user(db_session)
    material, _ = await _create_material(db_session, user)
    await db_session.commit()

    # An annotation on the document (version 1).
    ann_resp = await client.post(
        f"/api/materials/{material.id}/annotations",
        json={
            "body": "Important note",
            "selection_text": "sel",
            "position_data": {"startOffset": 0, "endOffset": 3, "textContent": "sel"},
            "page": 1,
        },
        headers=_auth_headers(user),
    )
    assert ann_resp.status_code == 201

    # A comment (chat) on the document.
    com_resp = await client.post(
        "/api/comments",
        json={
            "target_type": "material",
            "target_id": str(material.id),
            "body": "Nice document!",
        },
        headers=_auth_headers(user),
    )
    assert com_resp.status_code == 201

    # Simulate replacing the PDF: add version 2 and bump current_version.
    new_version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material.id,
        version_number=2,
        file_key="test/file2.pdf",
        file_name="file2.pdf",
        file_size=2048,
        file_mime_type="application/pdf",
    )
    db_session.add(new_version)
    material_db = await db_session.get(Material, material.id)
    assert material_db is not None
    material_db.current_version = 2
    await db_session.commit()

    # Annotations are still returned for the material.
    ann_list = await client.get(
        f"/api/materials/{material.id}/annotations",
        headers=_auth_headers(user),
    )
    assert ann_list.status_code == 200
    assert ann_list.json()["total"] >= 1

    # Comments are still returned for the material.
    com_list = await client.get(
        f"/api/comments?targetType=material&targetId={material.id}",
        headers=_auth_headers(user),
    )
    assert com_list.status_code == 200
    assert com_list.json()["total"] >= 1
