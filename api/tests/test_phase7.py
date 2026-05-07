import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment
from app.models.directory import Directory
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
        body="Test comment",
    )
    db.add(comment)
    await db.flush()
    return comment


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


# ----- Flag tests -----


async def test_create_flag(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    comment = await _create_comment(db_session, user, directory)
    await db_session.commit()

    res = await client.post(
        "/api/flags",
        json={
            "target_type": "comment",
            "target_id": str(comment.id),
            "reason": "spam",
            "description": "This is spam",
        },
        headers=_auth_headers(user),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["target_type"] == "comment"
    assert data["reason"] == "spam"
    assert data["status"] == "open"


async def test_create_flag_duplicate(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    comment = await _create_comment(db_session, user, directory)
    await db_session.commit()

    payload = {
        "target_type": "comment",
        "target_id": str(comment.id),
        "reason": "spam",
    }
    await client.post("/api/flags", json=payload, headers=_auth_headers(user))
    await db_session.commit()
    res = await client.post("/api/flags", json=payload, headers=_auth_headers(user))
    assert res.status_code == 400


async def test_create_flag_nonexistent_target(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    res = await client.post(
        "/api/flags",
        json={
            "target_type": "comment",
            "target_id": str(uuid.uuid4()),
            "reason": "spam",
        },
        headers=_auth_headers(user),
    )
    assert res.status_code == 404


async def test_list_flags_moderator_only(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _create_user(db_session, UserRole.STUDENT)
    mod = await _create_user(db_session, UserRole.MODERATOR)
    await db_session.commit()

    res = await client.get("/api/flags", headers=_auth_headers(student))
    assert res.status_code == 403

    res = await client.get("/api/flags", headers=_auth_headers(mod))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 0


async def test_update_flag_moderator_only(client: AsyncClient, db_session: AsyncSession) -> None:
    student = await _create_user(db_session, UserRole.STUDENT)
    mod = await _create_user(db_session, UserRole.MODERATOR)
    directory = await _create_directory(db_session, mod)
    comment = await _create_comment(db_session, student, directory)
    await db_session.commit()

    create_res = await client.post(
        "/api/flags",
        json={"target_type": "comment", "target_id": str(comment.id), "reason": "inappropriate"},
        headers=_auth_headers(student),
    )
    flag_id = create_res.json()["id"]

    res = await client.patch(
        f"/api/flags/{flag_id}",
        json={"status": "resolved"},
        headers=_auth_headers(student),
    )
    assert res.status_code == 403

    res = await client.patch(
        f"/api/flags/{flag_id}",
        json={"status": "resolved"},
        headers=_auth_headers(mod),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "resolved"
    assert res.json()["resolved_by"] is not None


async def test_list_flags_with_filters(client: AsyncClient, db_session: AsyncSession) -> None:
    mod = await _create_user(db_session, UserRole.MODERATOR)
    directory = await _create_directory(db_session, mod)
    comment = await _create_comment(db_session, mod, directory)
    await db_session.commit()

    await client.post(
        "/api/flags",
        json={"target_type": "comment", "target_id": str(comment.id), "reason": "spam"},
        headers=_auth_headers(mod),
    )
    await db_session.commit()

    res = await client.get("/api/flags?status=open", headers=_auth_headers(mod))
    assert res.status_code == 200
    assert res.json()["total"] == 1

    res = await client.get("/api/flags?status=resolved", headers=_auth_headers(mod))
    assert res.status_code == 200
    assert res.json()["total"] == 0

    res = await client.get("/api/flags?targetType=comment", headers=_auth_headers(mod))
    assert res.status_code == 200
    assert res.json()["total"] == 1


# ----- User profile tests -----


async def test_get_own_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    res = await client.get("/api/users/me", headers=_auth_headers(user))
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(user.id)
    assert "reputation" in data


async def test_patch_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    res = await client.patch(
        "/api/users/me",
        json={"display_name": "New Name", "bio": "Hello world"},
        headers=_auth_headers(user),
    )
    assert res.status_code == 200
    assert res.json()["display_name"] == "New Name"
    assert res.json()["bio"] == "Hello world"


async def test_get_public_profile(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    viewer = await _create_user(db_session)
    await db_session.commit()

    res = await client.get(f"/api/users/{user.id}", headers=_auth_headers(viewer))
    assert res.status_code == 200
    assert res.json()["id"] == str(user.id)


async def test_get_public_profile_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    res = await client.get(f"/api/users/{uuid.uuid4()}", headers=_auth_headers(user))
    assert res.status_code == 404


async def test_user_contributions_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    res = await client.get(
        f"/api/users/{user.id}/contributions?type=prs",
        headers=_auth_headers(user),
    )
    assert res.status_code == 200
    assert res.json()["total"] == 0


async def test_data_export(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    res = await client.get("/api/users/me/data-export", headers=_auth_headers(user))
    assert res.status_code == 200
    data = res.json()
    assert "profile" in data
    assert data["profile"]["id"] == str(user.id)
    assert "pull_requests" in data
    assert "annotations" in data
    assert "comments" in data
    assert "flags" in data


async def test_soft_delete_user(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    res = await client.delete("/api/users/me", headers=_auth_headers(user))
    assert res.status_code == 204
    await db_session.commit()

    res = await client.get("/api/users/me", headers=_auth_headers(user))
    assert res.status_code == 401
