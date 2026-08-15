from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.database.post_commit as post_commit_module
import app.core.events.sse as sse_module
import app.routers.annotations as annotations_router_module
from app.models.comment import Comment
from app.models.directory import Directory, DirectoryType
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PRComment, PullRequest
from app.models.user import User, UserRole


async def _create_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"pass-b-{uuid.uuid4()}@example.invalid",
        display_name="Pass B user",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _create_directory(db: AsyncSession, user: User) -> Directory:
    directory = Directory(
        id=uuid.uuid4(),
        name="Pass B directory",
        slug=f"pass-b-dir-{uuid.uuid4().hex[:12]}",
        type=DirectoryType.FOLDER,
        created_by=user.id,
    )
    db.add(directory)
    await db.flush()
    return directory


async def _create_material(
    db: AsyncSession,
    user: User,
    directory: Directory,
    *,
    with_version: bool = False,
) -> tuple[Material, MaterialVersion | None]:
    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Pass B material",
        slug=f"pass-b-material-{uuid.uuid4().hex[:12]}",
        type="document",
        current_version=1,
        author_id=user.id,
    )
    db.add(material)
    await db.flush()

    if not with_version:
        return material, None

    version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material.id,
        version_number=1,
        file_key="test/pass-b.pdf",
        file_name="pass-b.pdf",
        file_size=1024,
        file_mime_type="application/pdf",
    )
    db.add(version)
    await db.flush()
    return material, version


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("target_type", ["directory", "material"])
async def test_comment_listing_rejects_soft_deleted_parent(
    target_type: str,
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    if target_type == "directory":
        target = directory
    else:
        target, _ = await _create_material(db_session, user, directory)

    db_session.add(
        Comment(
            target_type=target_type,
            target_id=target.id,
            author_id=user.id,
            body="This must not survive parent visibility.",
        )
    )
    await db_session.flush()
    target.deleted_at = datetime.now(UTC)
    await db_session.commit()

    response = await client.get(
        f"/api/comments?targetType={target_type}&targetId={target.id}",
        headers=_auth_headers(user),
    )
    assert response.status_code == 404


async def test_annotation_sse_is_dispatched_only_after_commit(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material, _ = await _create_material(db_session, user, directory, with_version=True)
    await db_session.commit()

    order: list[str] = []
    original_commit = AsyncSession.commit

    async def tracked_commit(session: AsyncSession) -> None:
        await original_commit(session)
        order.append("commit")

    def tracked_broadcast(_topic: str, _event: dict[str, object]) -> None:
        order.append("broadcast")

    monkeypatch.setattr(AsyncSession, "commit", tracked_commit)
    # Production dispatch_post_commit_actions() uses the module-local binding in
    # app.core.database.post_commit. The hermetic client fixture imports the SSE
    # function directly when its DB override runs, so patch both bindings.
    monkeypatch.setattr(post_commit_module, "broadcast_to_topic", tracked_broadcast)
    monkeypatch.setattr(sse_module, "broadcast_to_topic", tracked_broadcast)
    # Guard against a future regression that reintroduces a direct router broadcast.
    monkeypatch.setattr(
        annotations_router_module,
        "broadcast_to_topic",
        tracked_broadcast,
        raising=False,
    )

    response = await client.post(
        f"/api/materials/{material.id}/annotations",
        json={
            "body": "Commit first, broadcast second",
            "selection_text": "Commit",
            "position_data": {
                "startOffset": 0,
                "endOffset": 6,
                "textContent": "Commit",
            },
            "page": 1,
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 201, response.text
    assert order.count("broadcast") == 1
    assert "commit" in order
    assert order.index("commit") < order.index("broadcast")


async def test_annotation_sse_is_not_dispatched_when_commit_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _create_user(db_session)
    directory = await _create_directory(db_session, user)
    material, _ = await _create_material(db_session, user, directory, with_version=True)
    await db_session.commit()

    broadcasts: list[tuple[str, dict[str, object]]] = []

    async def failing_commit(_session: AsyncSession) -> None:
        raise RuntimeError("forced commit failure")

    def tracked_broadcast(topic: str, event: dict[str, object]) -> None:
        broadcasts.append((topic, event))

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    monkeypatch.setattr(post_commit_module, "broadcast_to_topic", tracked_broadcast)
    monkeypatch.setattr(sse_module, "broadcast_to_topic", tracked_broadcast)
    monkeypatch.setattr(
        annotations_router_module,
        "broadcast_to_topic",
        tracked_broadcast,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="forced commit failure"):
        await client.post(
            f"/api/materials/{material.id}/annotations",
            json={
                "body": "This transaction must roll back",
                "selection_text": "roll back",
                "position_data": {
                    "startOffset": 0,
                    "endOffset": 9,
                    "textContent": "roll back",
                },
                "page": 1,
            },
            headers=_auth_headers(user),
        )

    assert broadcasts == []


async def test_pr_reply_parent_must_belong_to_same_pull_request(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    first_pr = PullRequest(
        title="First contribution",
        payload=[],
        summary_types=[],
        author_id=user.id,
    )
    second_pr = PullRequest(
        title="Second contribution",
        payload=[],
        summary_types=[],
        author_id=user.id,
    )
    db_session.add_all([first_pr, second_pr])
    await db_session.flush()

    foreign_parent = PRComment(
        pr_id=first_pr.id,
        author_id=user.id,
        body="Parent from the first PR",
    )
    db_session.add(foreign_parent)
    await db_session.commit()

    response = await client.post(
        f"/api/pull-requests/{second_pr.id}/comments",
        json={
            "body": "Cross-PR reply must be rejected",
            "parent_id": str(foreign_parent.id),
        },
        headers=_auth_headers(user),
    )
    assert response.status_code == 400
    assert "Parent comment must belong to this pull request" in response.json()["detail"]

    count = await db_session.scalar(
        select(func.count()).select_from(PRComment).where(PRComment.pr_id == second_pr.id)
    )
    assert count == 0
