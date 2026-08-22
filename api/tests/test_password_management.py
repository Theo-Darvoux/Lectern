from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security.security import create_access_token
from app.models.user import User, UserRole
from app.services.auth import get_password_hash


def _auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def test_passwordless_user_can_create_password_and_use_classic_login(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = User(
        email="passwordless@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
    )
    db_session.add(user)
    await db_session.flush()

    with patch.object(settings, "classic_auth_enabled", True):
        created = await client.post(
            "/api/auth/password",
            headers=_auth_headers(user),
            json={"password": "a-new-secure-password"},
        )

        assert created.status_code == 200
        assert created.json() == {"message": "Password created"}

        me = await client.get("/api/users/me", headers=_auth_headers(user))
        assert me.status_code == 200
        assert me.json()["has_password"] is True

        login = await client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "a-new-secure-password"},
        )

    assert login.status_code == 200
    assert login.json()["user"]["email"] == user.email


async def test_forgotten_password_can_be_reset_and_invalidates_old_sessions(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = User(
        email="forgotten@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
        password_hash=get_password_hash("the-old-password"),
    )
    db_session.add(user)
    await db_session.flush()
    old_headers = _auth_headers(user)

    with (
        patch.object(settings, "classic_auth_enabled", True),
        patch(
            "app.routers.auth.send_password_reset_email",
            new_callable=AsyncMock,
        ) as send_email,
    ):
        requested = await client.post(
            "/api/auth/password-reset/request",
            json={"email": user.email},
        )
        assert requested.status_code == 200
        assert requested.json() == {
            "message": "If an account exists, a password reset link has been sent"
        }

        reset_link = send_email.call_args.args[1]
        reset_token = reset_link.split("#token=", 1)[1]
        confirmed = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": reset_token, "password": "the-new-secure-password"},
        )

        assert confirmed.status_code == 200
        assert confirmed.json() == {"message": "Password reset"}
        new_login = await client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "the-new-secure-password"},
        )
        assert new_login.status_code == 200
        assert (
            await client.post(
                "/api/auth/login",
                json={"email": user.email, "password": "the-old-password"},
            )
        ).status_code == 401
        assert (await client.get("/api/users/me", headers=old_headers)).status_code == 401


async def test_password_creation_cannot_overwrite_existing_password(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = User(
        email="configured@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
        password_hash=get_password_hash("existing-password"),
    )
    db_session.add(user)
    await db_session.flush()

    with patch.object(settings, "classic_auth_enabled", True):
        response = await client.post(
            "/api/auth/password",
            headers=_auth_headers(user),
            json={"password": "replacement-password"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "This account already has a password"


async def test_password_reset_request_does_not_disclose_unknown_account(
    client: AsyncClient,
    fake_redis_setup,
) -> None:
    with (
        patch.object(settings, "classic_auth_enabled", True),
        patch(
            "app.routers.auth.send_password_reset_email",
            new_callable=AsyncMock,
        ) as send_email,
    ):
        response = await client.post(
            "/api/auth/password-reset/request",
            json={"email": "missing@example.com"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "message": "If an account exists, a password reset link has been sent"
    }
    send_email.assert_not_awaited()


async def test_password_reset_token_is_single_use(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = User(
        email="single-use@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
    )
    db_session.add(user)
    await db_session.flush()

    with (
        patch.object(settings, "classic_auth_enabled", True),
        patch(
            "app.routers.auth.send_password_reset_email",
            new_callable=AsyncMock,
        ) as send_email,
    ):
        await client.post(
            "/api/auth/password-reset/request",
            json={"email": user.email},
        )
        token = send_email.call_args.args[1].split("#token=", 1)[1]

        first = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": token, "password": "first-secure-password"},
        )
        replay = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": token, "password": "second-secure-password"},
        )

    assert first.status_code == 200
    assert replay.status_code == 400
    assert replay.json()["detail"] == "Invalid or expired password reset link"


async def test_new_password_reset_link_supersedes_the_previous_link(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = User(
        email="superseded-reset@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
    )
    db_session.add(user)
    await db_session.flush()

    with (
        patch.object(settings, "classic_auth_enabled", True),
        patch(
            "app.routers.auth.send_password_reset_email",
            new_callable=AsyncMock,
        ) as send_email,
    ):
        await client.post("/api/auth/password-reset/request", json={"email": user.email})
        first_token = send_email.call_args.args[1].split("#token=", 1)[1]
        send_email.reset_mock()
        await client.post("/api/auth/password-reset/request", json={"email": user.email})
        second_token = send_email.call_args.args[1].split("#token=", 1)[1]

        stale = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": first_token, "password": "stale-secure-password"},
        )
        current = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": second_token, "password": "current-secure-password"},
        )

    assert first_token != second_token
    assert stale.status_code == 400
    assert current.status_code == 200


async def test_password_management_is_unavailable_when_classic_login_is_disabled(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis_setup,
) -> None:
    user = User(
        email="disabled-classic@example.com",
        role=UserRole.STUDENT,
        onboarded=True,
        auto_approve=True,
    )
    db_session.add(user)
    await db_session.flush()

    with patch.object(settings, "classic_auth_enabled", False):
        create = await client.post(
            "/api/auth/password",
            headers=_auth_headers(user),
            json={"password": "a-secure-password"},
        )
        request = await client.post(
            "/api/auth/password-reset/request",
            json={"email": user.email},
        )

    assert create.status_code == 401
    assert request.status_code == 401
