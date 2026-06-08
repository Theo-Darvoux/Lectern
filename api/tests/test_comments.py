import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment
from app.models.directory import Directory
from app.models.user import User, UserRole


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Test User",
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


async def _create_comment(db: AsyncSession, user: User, directory: Directory) -> Comment:
    comment = Comment(
        target_type="directory",
        target_id=directory.id,
        author_id=user.id,
        body="Test comment body",
    )
    db.add(comment)
    await db.flush()
    return comment


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def test_list_comments_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    await db_session.commit()

    response = await client.get(
        f"/api/comments?targetType=directory&targetId={directory.id}",
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


async def test_create_comment(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    await db_session.commit()

    response = await client.post(
        "/api/comments",
        json={
            "target_type": "directory",
            "target_id": str(directory.id),
            "body": "Hello world!",
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["body"] == "Hello world!"
    assert data["target_type"] == "directory"
    assert data["target_id"] == str(directory.id)
    assert data["author_id"] == str(user.id)


async def test_create_comment_invalid_target(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    fake_id = str(uuid.uuid4())
    response = await client.post(
        "/api/comments",
        json={
            "target_type": "directory",
            "target_id": fake_id,
            "body": "Hello!",
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 404


async def test_edit_comment(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    comment = await _create_comment(db_session, user, directory)
    await db_session.commit()

    response = await client.patch(
        f"/api/comments/{comment.id}",
        json={"body": "Updated body"},
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    assert response.json()["body"] == "Updated body"


async def test_edit_comment_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    user1 = await _create_user(db_session)
    user2 = await _create_user(db_session)
    directory = await _create_directory(db_session, user1)
    comment = await _create_comment(db_session, user1, directory)
    await db_session.commit()

    response = await client.patch(
        f"/api/comments/{comment.id}",
        json={"body": "Hacked"},
        headers=_auth_headers(user2),
    )
    assert response.status_code == 403


async def test_delete_comment_author(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    comment = await _create_comment(db_session, user, directory)
    await db_session.commit()

    response = await client.delete(
        f"/api/comments/{comment.id}",
        headers=_auth_headers(user),
    )
    assert response.status_code == 204


async def test_delete_comment_moderator(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _create_user(db_session)
    mod = await _create_user(db_session, role=UserRole.MODERATOR)
    directory = await _create_directory(db_session, student)
    comment = await _create_comment(db_session, student, directory)
    await db_session.commit()

    response = await client.delete(
        f"/api/comments/{comment.id}",
        headers=_auth_headers(mod),
    )
    assert response.status_code == 204


async def test_delete_comment_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    user1 = await _create_user(db_session)
    user2 = await _create_user(db_session)
    directory = await _create_directory(db_session, user1)
    comment = await _create_comment(db_session, user1, directory)
    await db_session.commit()

    response = await client.delete(
        f"/api/comments/{comment.id}",
        headers=_auth_headers(user2),
    )
    assert response.status_code == 403


async def test_list_comments_with_data(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)

    for i in range(3):
        db_session.add(
            Comment(
                target_type="directory",
                target_id=directory.id,
                author_id=user.id,
                body=f"Comment {i}",
            )
        )
    await db_session.flush()
    await db_session.commit()

    response = await client.get(
        f"/api/comments?targetType=directory&targetId={directory.id}",
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3
