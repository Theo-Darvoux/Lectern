import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.user import User, UserRole
from app.models.view_history import ViewHistory


async def _create_user(
    db: AsyncSession,
    *,
    role: UserRole = UserRole.STUDENT,
    onboarded: bool = True,
    gdpr_consent: bool = True,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=role,
        onboarded=onboarded,
        gdpr_consent=gdpr_consent,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def test_get_me(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.get("/api/users/me", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == user.email
    assert data["display_name"] == "Tester"
    assert data["onboarded"] is True
    assert data["prs_approved"] == 0
    assert data["prs_total"] == 0
    assert data["annotations_count"] == 0
    assert data["comments_count"] == 0
    assert data["reputation"] == 0


async def test_get_me_unauthenticated(client: AsyncClient) -> None:
    response = await client.get("/api/users/me")
    assert response.status_code == 401


async def test_onboard_user(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session, onboarded=False, gdpr_consent=False)
    await db_session.commit()

    response = await client.post(
        "/api/users/me/onboard",
        json={
            "display_name": "New Name",
            "academic_year": "1A",
            "gdpr_consent": True,
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "New Name"
    assert data["onboarded"] is True


async def test_onboard_user_already_onboarded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session, onboarded=True)
    await db_session.commit()

    response = await client.post(
        "/api/users/me/onboard",
        json={
            "display_name": "Name",
            "academic_year": "2A",
            "gdpr_consent": True,
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 400


async def test_onboard_user_no_gdpr_consent(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session, onboarded=False, gdpr_consent=False)
    await db_session.commit()

    response = await client.post(
        "/api/users/me/onboard",
        json={
            "display_name": "Name",
            "academic_year": "1A",
            "gdpr_consent": False,
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 400


async def test_onboard_user_invalid_academic_year(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session, onboarded=False, gdpr_consent=False)
    await db_session.commit()

    response = await client.post(
        "/api/users/me/onboard",
        json={
            "display_name": "Name",
            "academic_year": "4A",
            "gdpr_consent": True,
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 422


async def test_onboard_user_unauthenticated(client: AsyncClient) -> None:
    response = await client.post(
        "/api/users/me/onboard",
        json={
            "display_name": "Name",
            "academic_year": "1A",
            "gdpr_consent": True,
        },
    )
    assert response.status_code == 401


async def test_recently_viewed_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.get("/api/users/me/recently-viewed", headers=_auth_headers(user))
    assert response.status_code == 200
    assert response.json() == []


async def test_recently_viewed_with_data(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    directory = Directory(
        id=uuid.uuid4(),
        name="Dir",
        slug="dir",
        type="folder",
        created_by=user.id,
    )
    db_session.add(directory)
    await db_session.flush()

    mat1 = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Material 1",
        slug="material-1",
        type="pdf",
        author_id=user.id,
    )
    mat2 = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Material 2",
        slug="material-2",
        type="pdf",
        author_id=user.id,
    )
    db_session.add_all([mat1, mat2])
    await db_session.flush()

    db_session.add(ViewHistory(user_id=user.id, material_id=mat1.id))
    db_session.add(ViewHistory(user_id=user.id, material_id=mat2.id))
    await db_session.flush()
    await db_session.commit()

    response = await client.get("/api/users/me/recently-viewed", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


async def test_recently_viewed_unauthenticated(client: AsyncClient) -> None:
    response = await client.get("/api/users/me/recently-viewed")
    assert response.status_code == 401


async def test_onboard_valid_academic_years(client: AsyncClient, db_session: AsyncSession) -> None:
    for year in ("1A", "2A", "3A+"):
        user = await _create_user(db_session, onboarded=False, gdpr_consent=False)
        await db_session.commit()

        response = await client.post(
            "/api/users/me/onboard",
            json={
                "display_name": f"Student {year}",
                "academic_year": year,
                "gdpr_consent": True,
            },
            headers=_auth_headers(user),
        )
        assert response.status_code == 200
        assert response.json()["academic_year"] == year
