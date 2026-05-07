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
        email=f"{uuid.uuid4().hex[:8]}@telecom-sudparis.eu",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

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
