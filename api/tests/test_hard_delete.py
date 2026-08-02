import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.annotation import Annotation
from app.models.comment import Comment
from app.models.pull_request import PullRequest
from app.models.upload import Upload
from app.models.user import User, UserRole


async def _create_user(db: AsyncSession, email_prefix: str = "test") -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{email_prefix}_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def test_hard_delete_self(client: AsyncClient, db_session: AsyncSession) -> None:
    # 1. Setup user with associated data
    user = await _create_user(db_session, "self")

    # Add an annotation
    from app.models.directory import Directory
    from app.models.material import Material

    directory = Directory(name="Test Dir", slug="test-dir", type="folder", created_by=user.id)
    db_session.add(directory)
    await db_session.flush()

    material = Material(
        directory_id=directory.id,
        title="Test Material",
        slug="test-material",
        type="pdf",
        author_id=user.id,
    )
    db_session.add(material)
    await db_session.flush()

    annotation = Annotation(material_id=material.id, author_id=user.id, body="Test Annotation")
    comment = Comment(
        target_type="material", target_id=material.id, author_id=user.id, body="Test Comment"
    )
    upload = Upload(
        upload_id=str(uuid.uuid4()), user_id=user.id, filename="test.pdf", status="completed"
    )
    pr = PullRequest(title="Test PR", type="batch", payload=[], author_id=user.id)

    db_session.add_all([annotation, comment, upload, pr])
    await db_session.commit()

    user_id = user.id
    mat_id = material.id
    pr_actual_id = pr.id
    headers = await _auth_headers(user)

    # 2. Perform deletion
    response = await client.delete("/api/users/me", headers=headers)
    assert response.status_code == 204

    db_session.expire_all()

    # 3. Verify user is gone
    res = await db_session.execute(select(User).where(User.id == user_id))
    assert res.scalar_one_or_none() is None

    # 4. Verify cascaded data is gone
    res = await db_session.execute(select(Annotation).where(Annotation.author_id == user_id))
    assert res.scalar_one_or_none() is None

    res = await db_session.execute(select(Comment).where(Comment.author_id == user_id))
    assert res.scalar_one_or_none() is None

    res = await db_session.execute(select(Upload).where(Upload.user_id == user_id))
    assert res.scalar_one_or_none() is None

    # 5. Verify Material and PR still exist but are UNSET
    res = await db_session.execute(select(Material).where(Material.id == mat_id))
    mat_after = res.scalar_one()
    assert mat_after.author_id is None

    res = await db_session.execute(select(PullRequest).where(PullRequest.author_id == user_id))
    assert res.scalar_one_or_none() is None

    res = await db_session.execute(select(PullRequest).where(PullRequest.id == pr_actual_id))
    pr_after = res.scalar_one()
    assert pr_after.author_id is None


async def test_admin_hard_delete_user(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await _create_user(db_session, "admin")
    admin.role = UserRole.BUREAU
    target = await _create_user(db_session, "target")
    await db_session.commit()

    headers = await _auth_headers(admin)
    target_id = target.id
    response = await client.delete(f"/api/admin/users/{target_id}", headers=headers)
    assert response.status_code == 200

    await db_session.commit()
    res = await db_session.execute(select(User).where(User.id == target_id))
    assert res.scalar_one_or_none() is None
