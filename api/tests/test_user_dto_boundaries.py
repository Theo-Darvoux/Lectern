import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.annotation import Annotation
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PRComment, PullRequest
from app.models.user import User, UserRole


async def _create_user(
    db: AsyncSession,
    *,
    name: str,
    role: UserRole = UserRole.STUDENT,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name=name,
        role=role,
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

    response = await client.get(f"/api/users/{user.id}", headers=_auth_headers(user))
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

    response = await client.get(
        f"/api/users/{user.id}/contributions?type=prs", headers=_auth_headers(user)
    )
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

    response = await client.get(
        f"/api/users/{author.id}/contributions?type=materials", headers=_auth_headers(author)
    )
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
    assert item["material_slug"] == material.slug
    assert item["material_title"] == "Annotated material"
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


async def test_guest_browse_projects_public_version_metadata(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    author = await _create_user(db_session, name="Browse Author")
    guest = await _create_user(db_session, name="Guest User", role=UserRole.GUEST)
    material = Material(
        title="Browse public projection",
        slug=f"browse-public-{uuid.uuid4().hex}",
        type="document",
        author_id=author.id,
        current_version=1,
    )
    db_session.add(material)
    await db_session.flush()
    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        file_key=f"materials/{material.id}/internal.pdf",
        file_name="visible.pdf",
        file_size=456,
        file_mime_type="application/pdf",
        version_lock=7,
    )
    db_session.add(version)
    await db_session.commit()

    guest_resp = await client.get("/api/browse", headers=_auth_headers(guest))
    assert guest_resp.status_code == 200
    public_item = next(
        item for item in guest_resp.json()["materials"] if item["id"] == str(material.id)
    )
    public_version = public_item["current_version_info"]
    assert public_version["file_name"] == "visible.pdf"
    assert public_version["file_mime_type"] == "application/pdf"
    for internal_field in (
        "file_key",
        "pr_id",
        "virus_scan_result",
        "version_lock",
        "author_id",
        "diff_summary",
    ):
        assert internal_field not in public_version

    authenticated = await client.get("/api/browse", headers=_auth_headers(author))
    assert authenticated.status_code == 200
    private_item = next(
        item for item in authenticated.json()["materials"] if item["id"] == str(material.id)
    )
    assert private_item["current_version_info"]["file_key"] == version.file_key
    assert private_item["current_version_info"]["version_lock"] == 7

    # Guest sessions are publicly mintable when enabled, so they must not be a
    # backdoor to the member-only storage/moderation projection.
    guest = User(
        id=uuid.uuid4(),
        email=f"guest-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Guest",
        role=UserRole.GUEST,
        onboarded=True,
        gdpr_consent=True,
    )
    db_session.add(guest)
    await db_session.commit()
    from app.core.security.security import create_access_token

    guest_token, _ = create_access_token(
        str(guest.id),
        guest.role.value,
        guest.email,
        session_id="public-browse-guest",
    )
    guest_response = await client.get(
        "/api/browse", headers={"Authorization": f"Bearer {guest_token}"}
    )
    assert guest_response.status_code == 200
    guest_item = next(
        item for item in guest_response.json()["materials"] if item["id"] == str(material.id)
    )
    assert "file_key" not in guest_item["current_version_info"]
    assert "version_lock" not in guest_item["current_version_info"]


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


async def test_guest_material_detail_popular_and_home_use_public_projection(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A freely mintable guest identity must not recover member-only DTO fields."""
    author = await _create_user(db_session, name="Guest Boundary Author")
    guest = User(
        id=uuid.uuid4(),
        email=f"guest-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Guest",
        role=UserRole.GUEST,
        onboarded=True,
        gdpr_consent=True,
    )
    material = Material(
        title="Guest public material",
        slug=f"guest-public-{uuid.uuid4().hex}",
        type="document",
        author_id=author.id,
        current_version=1,
        views_today=1_000_000,
        views_14d=1_000_000,
    )
    db_session.add_all([guest, material])
    await db_session.flush()
    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        file_key=f"materials/{material.id}/member-only.pdf",
        file_name="public-name.pdf",
        file_size=789,
        file_mime_type="application/pdf",
        version_lock=11,
    )
    pr = PullRequest(
        type="batch",
        title="Guest home public PR",
        description="summary",
        payload=[{"op": "delete_material", "material_id": str(material.id)}],
        summary_types=["delete_material"],
        author_id=author.id,
    )
    db_session.add_all([version, pr])
    await db_session.commit()

    from app.core.security.security import create_access_token

    guest_token, _ = create_access_token(
        str(guest.id),
        guest.role.value,
        guest.email,
        session_id="guest-public-dto-boundary",
    )
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    private_version_fields = (
        "file_key",
        "pr_id",
        "virus_scan_result",
        "version_lock",
        "author_id",
        "diff_summary",
    )

    detail = await client.get(f"/api/materials/{material.id}", headers=guest_headers)
    assert detail.status_code == 200
    guest_version = detail.json()["current_version_info"]
    assert guest_version["file_name"] == "public-name.pdf"
    for field in private_version_fields:
        assert field not in guest_version

    version_list = await client.get(f"/api/materials/{material.id}/versions", headers=guest_headers)
    assert version_list.status_code == 200
    assert len(version_list.json()) == 1
    guest_history_version = version_list.json()[0]
    assert guest_history_version["file_name"] == "public-name.pdf"
    for field in private_version_fields:
        assert field not in guest_history_version

    version_detail = await client.get(
        f"/api/materials/{material.id}/versions/1", headers=guest_headers
    )
    assert version_detail.status_code == 200
    guest_specific_version = version_detail.json()
    assert guest_specific_version["file_name"] == "public-name.pdf"
    for field in private_version_fields:
        assert field not in guest_specific_version

    popular = await client.get("/api/home/popular?period=today&limit=50", headers=guest_headers)
    assert popular.status_code == 200
    popular_item = next(item for item in popular.json() if item["id"] == str(material.id))
    assert "file_key" not in popular_item["current_version_info"]
    assert "version_lock" not in popular_item["current_version_info"]

    home = await client.get("/api/home/", headers=guest_headers)
    assert home.status_code == 200
    home_body = home.json()
    home_item = next(item for item in home_body["popular_today"] if item["id"] == str(material.id))
    assert "file_key" not in home_item["current_version_info"]
    assert "version_lock" not in home_item["current_version_info"]
    home_pr = next(item for item in home_body["recent_prs"] if item["id"] == str(pr.id))
    assert "payload" not in home_pr
    assert "applied_result" not in home_pr

    member_headers = _auth_headers(author)
    member = await client.get(f"/api/materials/{material.id}", headers=member_headers)
    assert member.status_code == 200
    assert member.json()["current_version_info"]["file_key"] == version.file_key
    assert member.json()["current_version_info"]["version_lock"] == 11

    member_versions = await client.get(
        f"/api/materials/{material.id}/versions", headers=member_headers
    )
    assert member_versions.status_code == 200
    assert member_versions.json()[0]["file_key"] == version.file_key
    assert member_versions.json()[0]["version_lock"] == 11

    member_version = await client.get(
        f"/api/materials/{material.id}/versions/1", headers=member_headers
    )
    assert member_version.status_code == 200
    assert member_version.json()["file_key"] == version.file_key
    assert member_version.json()["version_lock"] == 11
