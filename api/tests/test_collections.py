import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.security import create_access_token
from app.models.directory import Directory, DirectoryFavourite, DirectoryType
from app.models.material import Material, MaterialFavourite
from app.models.user import User, UserRole


def _auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def _create_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Collections Tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _create_targets(db: AsyncSession, user: User) -> tuple[Directory, Material]:
    directory = Directory(
        id=uuid.uuid4(),
        parent_id=None,
        name="Algorithms",
        slug="algorithms",
        type=DirectoryType.MODULE,
        created_by=user.id,
    )
    db.add(directory)
    await db.flush()
    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Graph theory notes",
        slug="graph-theory-notes",
        type="pdf",
        author_id=user.id,
    )
    db.add(material)
    await db.flush()
    return directory, material


async def test_saved_library_contains_material_and_directory_favourites(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    directory, material = await _create_targets(db_session, user)
    db_session.add_all(
        [
            DirectoryFavourite(user_id=user.id, directory_id=directory.id),
            MaterialFavourite(user_id=user.id, material_id=material.id),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/users/me/saved", headers=_auth_headers(user))

    assert response.status_code == 200
    items = response.json()["items"]
    assert {(item["target_type"], item["title"]) for item in items} == {
        ("directory", "Algorithms"),
        ("material", "Graph theory notes"),
    }
    material_item = next(item for item in items if item["target_type"] == "material")
    directory_item = next(item for item in items if item["target_type"] == "directory")
    assert material_item["href"] == "/browse/algorithms/graph-theory-notes"
    assert directory_item["href"] == "/browse/algorithms"


async def test_collection_crud_and_mixed_membership(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session)
    directory, material = await _create_targets(db_session, user)
    await db_session.commit()
    headers = _auth_headers(user)

    create = await client.post(
        "/api/collections",
        headers=headers,
        json={"name": "  Exam   revision  "},
    )
    assert create.status_code == 201
    collection = create.json()
    assert collection["name"] == "Exam revision"
    collection_id = collection["id"]

    duplicate = await client.post(
        "/api/collections", headers=headers, json={"name": "exam revision"}
    )
    assert duplicate.status_code == 409

    for target_type, target_id in (
        ("material", material.id),
        ("directory", directory.id),
    ):
        response = await client.post(
            f"/api/collections/{collection_id}/items",
            headers=headers,
            json={"target_type": target_type, "target_id": str(target_id)},
        )
        assert response.status_code == 204
        # Adding the same item twice is intentionally idempotent.
        response = await client.post(
            f"/api/collections/{collection_id}/items",
            headers=headers,
            json={"target_type": target_type, "target_id": str(target_id)},
        )
        assert response.status_code == 204

    detail = await client.get(f"/api/collections/{collection_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["item_count"] == 2
    assert {item["target_type"] for item in detail.json()["items"]} == {
        "material",
        "directory",
    }

    membership = await client.get(
        "/api/collections",
        headers=headers,
        params={"target_type": "material", "target_id": str(material.id)},
    )
    assert membership.status_code == 200
    assert membership.json()[0]["contains_target"] is True

    rename = await client.patch(
        f"/api/collections/{collection_id}",
        headers=headers,
        json={"name": "Must read"},
    )
    assert rename.status_code == 200
    assert rename.json()["name"] == "Must read"

    remove = await client.delete(
        f"/api/collections/{collection_id}/items/material/{material.id}", headers=headers
    )
    assert remove.status_code == 204
    detail = await client.get(f"/api/collections/{collection_id}", headers=headers)
    assert detail.json()["item_count"] == 1

    delete = await client.delete(f"/api/collections/{collection_id}", headers=headers)
    assert delete.status_code == 204
    assert (
        await client.get(f"/api/collections/{collection_id}", headers=headers)
    ).status_code == 404


async def test_collections_are_private_to_the_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _create_user(db_session)
    other = await _create_user(db_session)
    await db_session.commit()

    create = await client.post(
        "/api/collections",
        headers=_auth_headers(owner),
        json={"name": "Private list"},
    )
    collection_id = create.json()["id"]

    response = await client.get(f"/api/collections/{collection_id}", headers=_auth_headers(other))
    assert response.status_code == 404
    response = await client.delete(
        f"/api/collections/{collection_id}", headers=_auth_headers(other)
    )
    assert response.status_code == 404
