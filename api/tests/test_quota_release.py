import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.upload import Upload
from app.models.user import User, UserRole
from app.routers.upload.helpers import _QUOTA_KEY_PREFIX


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
    await db.flush()
    return user


@pytest.fixture
def mock_storage(mock_redis):
    with (
        patch("app.services.pr.object_exists", new_callable=AsyncMock) as m,
        patch("app.core.redis.redis_client", mock_redis),
    ):
        m.return_value = True
        yield m


def _auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_quota_released_on_pr_approval(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup, mock_storage
):
    user = await _create_user(db_session)
    admin = await _create_user(db_session, UserRole.BUREAU)
    await db_session.commit()

    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/test.pdf"
    staging_key = f"staging:{user.id}:{upload_id}"

    # 1. Add to quota
    await fake_redis_setup.zadd(f"{_QUOTA_KEY_PREFIX}{user.id}", {quarantine_key: time.time()})
    await fake_redis_setup.zadd(f"{_QUOTA_KEY_PREFIX}{user.id}", {staging_key: time.time()})

    # 2. Create Upload record
    up = Upload(
        upload_id=upload_id,
        user_id=user.id,
        quarantine_key=quarantine_key,
        final_key="cas/somehash",
        filename="test.pdf",
        mime_type="application/pdf",
        status="clean",
    )
    db_session.add(up)
    await db_session.commit()

    # 3. Create PR
    pr_resp = await client.post(
        "/api/pull-requests",
        headers=_auth_headers(user),
        json={
            "title": "Test PR",
            "operations": [
                {
                    "op": "create_material",
                    "title": "New Material",
                    "type": "document",
                    "file_key": "cas/somehash",
                    "file_name": "test.pdf",
                }
            ],
        },
    )
    assert pr_resp.status_code == 201
    pr_id = pr_resp.json()["id"]

    # Verify quota is still there
    assert await fake_redis_setup.zcard(f"{_QUOTA_KEY_PREFIX}{user.id}") == 2

    # 4. Approve PR
    url = f"/api/pull-requests/{pr_id}/approve"
    app_resp = await client.post(url, headers=_auth_headers(admin))
    assert app_resp.status_code == 200

    # 5. Verify quota released
    assert await fake_redis_setup.zcard(f"{_QUOTA_KEY_PREFIX}{user.id}") == 0

    # 6. Verify Upload status updated to 'applied'
    await db_session.refresh(up)
    assert up.status == "applied"


@pytest.mark.asyncio
async def test_quota_released_on_pr_rejection(
    client: AsyncClient, db_session: AsyncSession, fake_redis_setup, mock_storage
):
    user = await _create_user(db_session)
    admin = await _create_user(db_session, UserRole.BUREAU)
    await db_session.commit()

    upload_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user.id}/{upload_id}/test.pdf"

    await fake_redis_setup.zadd(f"{_QUOTA_KEY_PREFIX}{user.id}", {quarantine_key: time.time()})

    up = Upload(
        upload_id=upload_id,
        user_id=user.id,
        quarantine_key=quarantine_key,
        final_key="cas/somehash",
        filename="test.pdf",
        mime_type="application/pdf",
        status="clean",
    )
    db_session.add(up)
    await db_session.commit()

    pr_resp = await client.post(
        "/api/pull-requests",
        headers=_auth_headers(user),
        json={
            "title": "Test PR",
            "operations": [
                {
                    "op": "create_material",
                    "title": "New Material",
                    "type": "document",
                    "file_key": "cas/somehash",
                    "file_name": "test.pdf",
                }
            ],
        },
    )
    assert pr_resp.status_code == 201
    pr_id = pr_resp.json()["id"]

    # 4. Reject PR
    url = f"/api/pull-requests/{pr_id}/reject"
    rej_resp = await client.post(
        url, headers=_auth_headers(admin), json={"reason": "Inappropriate content detected"}
    )
    assert rej_resp.status_code == 200

    # Verify quota released
    assert await fake_redis_setup.zcard(f"{_QUOTA_KEY_PREFIX}{user.id}") == 0
