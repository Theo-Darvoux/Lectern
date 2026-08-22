import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.security import create_access_token
from app.models.annotation import Annotation
from app.models.material import Material
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.services.leaderboard import get_period_start


def _auth_headers(user: User) -> dict[str, str]:
    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


async def _user(
    db: AsyncSession,
    name: str,
    *,
    academic_year: str = "1A",
    role: UserRole = UserRole.STUDENT,
    is_flagged: bool = False,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        display_name=name,
        academic_year=academic_year,
        role=role,
        onboarded=True,
        gdpr_consent=True,
        is_flagged=is_flagged,
    )
    db.add(user)
    await db.flush()
    return user


async def _approved_pr(
    db: AsyncSession,
    user: User,
    approved_at: datetime,
    *,
    contribution_type: str = "batch",
) -> PullRequest:
    contribution = PullRequest(
        title=f"Contribution by {user.display_name}",
        type=contribution_type,
        status=PRStatus.APPROVED,
        payload=[],
        summary_types=[],
        author_id=user.id,
        approved_at=approved_at,
    )
    db.add(contribution)
    await db.flush()
    return contribution


async def _annotations(db: AsyncSession, user: User, count: int, created_at: datetime) -> None:
    material = Material(
        title=f"Material for {user.display_name}",
        slug=f"material-{uuid.uuid4().hex}",
        type="document",
        author_id=user.id,
    )
    db.add(material)
    await db.flush()
    db.add_all(
        Annotation(
            material_id=material.id,
            author_id=user.id,
            body=f"Useful note {index}",
            created_at=created_at,
        )
        for index in range(count)
    )


async def test_leaderboard_scores_filters_and_returns_my_rank(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    alice = await _user(db_session, "Alice", academic_year="1A")
    bob = await _user(db_session, "Bob", academic_year="2A")
    zoe = await _user(db_session, "Zoe", academic_year="1A")
    flagged = await _user(db_session, "Flagged", is_flagged=True)
    guest = await _user(db_session, "Guest", role=UserRole.GUEST)

    await _approved_pr(db_session, alice, now)
    await _approved_pr(db_session, alice, now)
    await _annotations(db_session, alice, 1, now)

    await _approved_pr(db_session, bob, now)
    await _approved_pr(db_session, bob, now)
    await _approved_pr(db_session, bob, now)
    await _approved_pr(db_session, bob, now - timedelta(days=400))
    await _annotations(db_session, bob, 8, now)

    await _approved_pr(db_session, zoe, now)
    await _approved_pr(db_session, zoe, now)
    await _annotations(db_session, zoe, 20, now)

    reverted = await _approved_pr(db_session, alice, now)
    revert = await _approved_pr(db_session, alice, now, contribution_type="revert")
    reverted.reverted_by_pr_id = revert.id
    revert.reverts_pr_id = reverted.id

    await _approved_pr(db_session, flagged, now)
    await _approved_pr(db_session, guest, now)
    await db_session.commit()

    monthly = await client.get(
        "/api/leaderboard?period=month&limit=1&page=1",
        headers=_auth_headers(alice),
    )
    assert monthly.status_code == 200
    payload = monthly.json()
    assert payload["period"] == "month"
    assert payload["total"] == 3
    assert payload["pages"] == 3
    assert payload["items"] == [
        {
            "rank": 1,
            "user_id": str(bob.id),
            "display_name": "Bob",
            "avatar_url": None,
            "academic_year": "2A",
            "approved_contributions": 3,
            "annotations": 8,
            "score": 30,
        }
    ]
    assert payload["current_user"]["rank"] == 2
    assert payload["current_user"]["score"] == 20

    tied_page = await client.get(
        "/api/leaderboard?period=month&limit=1&page=2",
        headers=_auth_headers(alice),
    )
    assert tied_page.status_code == 200
    assert tied_page.json()["items"][0]["user_id"] == str(alice.id)

    all_time = await client.get(
        "/api/leaderboard?period=all_time&academic_year=2A",
        headers=_auth_headers(alice),
    )
    assert all_time.status_code == 200
    assert all_time.json()["items"][0]["approved_contributions"] == 4
    assert all_time.json()["items"][0]["score"] == 40
    assert all_time.json()["current_user"] is None


async def test_leaderboard_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/leaderboard")
    assert response.status_code == 401


def test_academic_semester_boundaries() -> None:
    assert get_period_start("semester", datetime(2026, 8, 22, tzinfo=UTC)) == datetime(
        2026, 8, 1, tzinfo=UTC
    )
    assert get_period_start("semester", datetime(2026, 1, 15, tzinfo=UTC)) == datetime(
        2025, 8, 1, tzinfo=UTC
    )
    assert get_period_start("semester", datetime(2026, 2, 1, tzinfo=UTC)) == datetime(
        2026, 2, 1, tzinfo=UTC
    )
