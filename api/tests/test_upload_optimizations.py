import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.commit()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def _create_directory(db: AsyncSession, name: str, user_id: uuid.UUID) -> Directory:
    d = Directory(id=uuid.uuid4(), name=name, slug=name.lower(), type="folder", created_by=user_id)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


@pytest.mark.asyncio
async def test_upload_idempotency(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: AsyncMock,
    mock_arq_pool: AsyncMock,
):
    """Verify that multiple uploads with the same X-Upload-ID return the same result."""
    user = await _create_user(db_session)

    upload_id = str(uuid.uuid4())
    file_content = b"%PDF-1.4\ntest content"
    files = {"file": ("test.pdf", file_content, "application/pdf")}
    headers = {**_auth_headers(user), "X-Upload-ID": upload_id}

    with (
        patch("app.routers.upload.direct.get_s3_client") as mock_s3_cm,
        patch("app.dependencies.auth.is_token_blacklisted", new_callable=AsyncMock) as mock_bl,
        patch("app.dependencies.auth.is_session_revoked", new_callable=AsyncMock) as mock_session,
    ):
        mock_s3 = AsyncMock()
        mock_s3_cm.return_value.__aenter__.return_value = mock_s3
        mock_bl.return_value = False
        mock_session.return_value = False

        # 1. First upload — goes through the full pipeline
        resp1 = await client.post("/api/upload", files=files, headers=headers)
        assert resp1.status_code == 202
        data1 = resp1.json()

        # 2. Second upload with same ID — Redis cache hit, returns immediately
        mock_redis.get.return_value = json.dumps(data1)
        resp2 = await client.post("/api/upload", files=files, headers=headers)
        assert resp2.status_code == 202
        assert resp2.json() == data1
        # Processing was only enqueued once (first upload)
        assert mock_arq_pool.enqueue_job.call_count == 1


@pytest.mark.asyncio
async def test_atomic_pr_application_copy_not_move(db_session: AsyncSession):
    """Verify that apply_pr copies files and enqueues deletion."""
    from app.services.pr import apply_pr

    student = await _create_user(db_session, UserRole.STUDENT)
    await _create_user(db_session, UserRole.BUREAU)
    d = await _create_directory(db_session, "Dir", user_id=student.id)

    file_key = f"uploads/{student.id}/{uuid.uuid4()}/test.pdf"

    # Create a PR in DB
    pr = PullRequest(
        id=uuid.uuid4(),
        title="Atomic Test",
        status=PRStatus.OPEN,
        author_id=student.id,
        payload=[
            {
                "op": "create_material",
                "directory_id": str(d.id),
                "title": "NewMat",
                "type": "document",
                "file_key": file_key,
                "file_name": "test.pdf",
            }
        ],
    )
    db_session.add(pr)
    await db_session.commit()
    await db_session.refresh(pr)

    db_session.info["post_commit_jobs"] = []

    with (
        patch("app.services.pr.copy_object", new_callable=AsyncMock) as mock_copy,
        patch("app.services.pr.get_object_info", new_callable=AsyncMock) as mock_info,
    ):
        mock_info.return_value = {"size": 100, "content_type": "application/pdf"}

        # Apply PR
        await apply_pr(db_session, pr)

        # Verify: copy_object was called (to destination)
        assert mock_copy.call_count == 1
        call_args = mock_copy.call_args[0]
        assert call_args[0] == file_key
        assert "materials/" in call_args[1]

        # Verify: delete and index jobs were enqueued to session.info
        jobs = db_session.info["post_commit_jobs"]
        assert any(j[0] == "delete_storage_objects" and j[1] == [file_key] for j in jobs)
        assert any(j[0] == "index_material" for j in jobs)


@pytest.mark.asyncio
async def test_slug_generation_no_race(db_session: AsyncSession):
    """Verify slug generation doesn't skip locks."""
    from app.services.pr import _unique_material_slug

    user = await _create_user(db_session)
    d = await _create_directory(db_session, "Dir", user_id=user.id)

    slug1 = await _unique_material_slug(db_session, d.id, "My Notes")
    assert slug1 == "my-notes"

    m1 = Material(
        id=uuid.uuid4(),
        directory_id=d.id,
        title="My Notes",
        slug=slug1,
        author_id=user.id,
        type="document",
    )
    db_session.add(m1)
    await db_session.flush()

    slug2 = await _unique_material_slug(db_session, d.id, "My Notes")
    assert slug2 == "my-notes-2"
