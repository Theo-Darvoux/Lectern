import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.annotation import Annotation
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PRComment, PullRequest
from app.models.user import User, UserRole


async def _create_user(db: AsyncSession, *, name: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name=name,
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
        academic_year="2A",
        bio="Public bio",
        auto_approve=False,
        completed_tutorials=["welcome"],
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def test_self_profile_keeps_private_account_state(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session, name="Owner")
    await db_session.commit()

    response = await client.get("/api/users/me", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == user.email
    assert data["onboarded"] is True
    assert data["auto_approve"] is False
    assert data["completed_tutorials"] == ["welcome"]
    assert "open_pr_count" in data


async def test_public_profile_excludes_private_account_state(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session, name="Public User")
    await db_session.commit()

    # This route is intentionally unauthenticated. The response must be built
    # from the public DTO rather than filtering a private DTO after the fact.
    response = await client.get(f"/api/users/{user.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(user.id)
    assert data["display_name"] == "Public User"
    assert data["bio"] == "Public bio"
    assert data["academic_year"] == "2A"
    assert data["role"] == UserRole.STUDENT.value
    assert "created_at" in data
    assert "email" not in data
    assert "onboarded" not in data
    assert "auto_approve" not in data
    assert "completed_tutorials" not in data
    assert "open_pr_count" not in data


async def test_public_pr_contribution_author_is_minimal(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session, name="Contributor")
    pr = PullRequest(
        type="batch",
        title="Public contribution",
        description=None,
        payload=[],
        summary_types=[],
        author_id=user.id,
    )
    db_session.add(pr)
    await db_session.commit()

    response = await client.get(f"/api/users/{user.id}/contributions?type=prs")
    assert response.status_code == 200
    item = response.json()["items"][0]
    author = item["author"]
    assert author == {
        "id": str(user.id),
        "display_name": "Contributor",
        "avatar_url": None,
    }
    assert set(item) == {
        "id",
        "type",
        "status",
        "title",
        "description",
        "summary_types",
        "author",
        "created_at",
        "updated_at",
    }
    assert "payload" not in item
    assert "applied_result" not in item
    assert "reviewed_by" not in item
    assert "virus_scan_result" not in item
    assert "rejection_reason" not in item


async def test_public_material_contribution_does_not_expose_version_storage_metadata(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    author = await _create_user(db_session, name="Material Author")
    material = Material(
        title="Public material contribution",
        slug=f"public-material-{uuid.uuid4().hex}",
        type="document",
        author_id=author.id,
    )
    db_session.add(material)
    await db_session.flush()
    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        file_key=f"materials/{material.id}/secret.pdf",
        file_name="public.pdf",
        file_size=123,
        file_mime_type="application/pdf",
        pr_id=None,
    )
    db_session.add(version)
    await db_session.commit()

    response = await client.get(f"/api/users/{author.id}/contributions?type=materials")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == str(material.id)
    assert "current_version_info" not in item
    assert "file_key" not in item
    assert "virus_scan_result" not in item
    assert "version_lock" not in item


async def test_annotation_contributions_require_auth_and_hide_deleted_parent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    author = await _create_user(db_session, name="Annotation Author")
    viewer = await _create_user(db_session, name="Viewer")
    material = Material(
        title="Annotated material",
        slug=f"annotated-{uuid.uuid4().hex}",
        type="document",
        author_id=author.id,
    )
    db_session.add(material)
    await db_session.flush()
    annotation = Annotation(
        material_id=material.id,
        author_id=author.id,
        body="private annotation body",
        selection_text="selected text",
        position_data={"x": 1},
    )
    db_session.add(annotation)
    await db_session.commit()

    anonymous = await client.get(f"/api/users/{author.id}/contributions?type=annotations")
    assert anonymous.status_code == 401

    visible = await client.get(
        f"/api/users/{author.id}/contributions?type=annotations",
        headers=_auth_headers(viewer),
    )
    assert visible.status_code == 200
    item = visible.json()["items"][0]
    assert item["body"] == "private annotation body"
    assert "selection_text" not in item
    assert "position_data" not in item
    assert "page" not in item

    material.deleted_at = annotation.created_at
    await db_session.commit()

    hidden = await client.get(
        f"/api/users/{author.id}/contributions?type=annotations",
        headers=_auth_headers(viewer),
    )
    assert hidden.status_code == 200
    assert hidden.json()["items"] == []
    assert hidden.json()["total"] == 0


async def test_pr_comment_author_is_minimal_for_other_users(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    author = await _create_user(db_session, name="Comment Author")
    viewer = await _create_user(db_session, name="Viewer")
    pr = PullRequest(
        type="batch",
        title="Commented contribution",
        description=None,
        payload=[],
        summary_types=[],
        author_id=author.id,
    )
    db_session.add(pr)
    await db_session.flush()
    db_session.add(PRComment(pr_id=pr.id, author_id=author.id, body="Hello"))
    await db_session.commit()

    response = await client.get(
        f"/api/pull-requests/{pr.id}/comments",
        headers=_auth_headers(viewer),
    )
    assert response.status_code == 200
    nested_author = response.json()[0]["author"]
    assert nested_author == {
        "id": str(author.id),
        "display_name": "Comment Author",
        "avatar_url": None,
    }
