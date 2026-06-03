import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.download_audit import DownloadAudit
from app.models.material import Material
from app.models.user import User, UserRole
from app.services.audit import flag_user_account, record_download


async def _create_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@test.example",
        display_name="Tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _create_material(db: AsyncSession) -> Material:
    material = Material(
        id=uuid.uuid4(),
        title="Test Doc",
        slug=f"test-{uuid.uuid4().hex[:8]}",
        type="document",
        current_version=1,
    )
    db.add(material)
    await db.flush()
    return material


async def test_record_download_creates_db_row(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material = await _create_material(db_session)

    await record_download(
        db_session,
        user_id=user.id,
        material_id=material.id,
        version_number=1,
        ip_address="127.0.0.1",
        user_agent="pytest/1.0",
    )
    await db_session.flush()

    rows = (
        (
            await db_session.execute(
                select(DownloadAudit).where(
                    DownloadAudit.user_id == user.id,
                    DownloadAudit.material_id == material.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.version_number == 1
    assert row.ip_address == "127.0.0.1"
    assert row.user_agent == "pytest/1.0"


async def test_record_download_optional_fields_nullable(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    material = await _create_material(db_session)

    await record_download(db_session, user_id=user.id, material_id=material.id)
    await db_session.flush()

    row = (
        await db_session.execute(
            select(DownloadAudit).where(
                DownloadAudit.user_id == user.id,
                DownloadAudit.material_id == material.id,
            )
        )
    ).scalar_one()
    assert row.version_number is None
    assert row.ip_address is None
    assert row.user_agent is None


async def test_record_download_multiple_events_for_same_material(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    material = await _create_material(db_session)

    await record_download(db_session, user_id=user.id, material_id=material.id, version_number=1)
    await record_download(db_session, user_id=user.id, material_id=material.id, version_number=2)
    await db_session.flush()

    rows = (
        (
            await db_session.execute(
                select(DownloadAudit).where(DownloadAudit.material_id == material.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2


async def test_flag_user_account_sets_flag(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    assert not user.is_flagged

    await flag_user_account(db_session, user_id=user.id, reason="suspicious activity")
    await db_session.flush()
    await db_session.refresh(user)

    assert user.is_flagged is True
    assert user.flag_reason == "suspicious activity"


async def test_flag_user_account_overwrites_previous_reason(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)

    await flag_user_account(db_session, user_id=user.id, reason="first reason")
    await db_session.flush()
    await flag_user_account(db_session, user_id=user.id, reason="updated reason")
    await db_session.flush()
    await db_session.refresh(user)

    assert user.flag_reason == "updated reason"
