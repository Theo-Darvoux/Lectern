import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.user import User, UserRole


async def _create_user(
    db: AsyncSession,
    *,
    role: UserRole = UserRole.STUDENT,
) -> User:
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


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_mark_notification_read_patch(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    notif = Notification(user_id=user.id, type="test", title="Test Notification", read=False)
    db_session.add(notif)
    await db_session.flush()
    await db_session.commit()

    # This is expected to fail with 405 before the fix
    response = await client.patch(
        f"/api/notifications/{notif.id}/read", headers=_auth_headers(user)
    )

    # We want it to be 200 after the fix.
    # For reproduction, we'll just check if it's NOT 405 after we apply the fix.
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Verify in DB
    await db_session.refresh(notif)
    assert notif.read is True


@pytest.mark.asyncio
async def test_mark_all_read(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    notifs = [
        Notification(user_id=user.id, type="test", title=f"Test {i}", read=False) for i in range(3)
    ]
    db_session.add_all(notifs)
    await db_session.flush()
    await db_session.commit()

    response = await client.post("/api/notifications/read-all", headers=_auth_headers(user))
    assert response.status_code == 200
    assert response.json()["marked"] == 3


async def _create_material_with(db: AsyncSession, author: User):
    """Minimal material (+ directory + v1) owned by `author`, for subscriber tests."""
    from app.models.directory import Directory
    from app.models.material import Material, MaterialVersion

    directory = Directory(
        id=uuid.uuid4(),
        name="Dir",
        slug=f"dir-{uuid.uuid4().hex[:6]}",
        type="folder",
        created_by=author.id,
    )
    db.add(directory)
    await db.flush()
    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Doc",
        slug=f"doc-{uuid.uuid4().hex[:6]}",
        type="document",
        current_version=1,
        author_id=author.id,
    )
    db.add(material)
    await db.flush()
    db.add(
        MaterialVersion(
            id=uuid.uuid4(),
            material_id=material.id,
            version_number=1,
            file_key="k.pdf",
            file_name="k.pdf",
            file_size=1,
            file_mime_type="application/pdf",
        )
    )
    await db.flush()
    return material


async def test_comment_notifies_subscribers_not_actor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Posting a comment on a material notifies its author and likers, but never
    the user who posted, and at most once per recipient."""
    from app.models.material import MaterialLike

    author = await _create_user(db_session)  # subscribed: material author
    liker = await _create_user(db_session)  # subscribed: liked the material
    commenter = await _create_user(db_session)  # the actor — must NOT be notified

    material = await _create_material_with(db_session, author)
    db_session.add(MaterialLike(user_id=liker.id, material_id=material.id))
    await db_session.commit()

    resp = await client.post(
        "/api/comments",
        json={
            "target_type": "material",
            "target_id": str(material.id),
            "body": "Great doc!",
        },
        headers=_auth_headers(commenter),
    )
    assert resp.status_code == 201

    # Author and liker each got exactly one notification; commenter got none.
    for subscriber in (author, liker):
        r = await client.get("/api/notifications", headers=_auth_headers(subscriber))
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["type"] == "material_comment"

    r = await client.get("/api/notifications", headers=_auth_headers(commenter))
    assert r.json()["total"] == 0
