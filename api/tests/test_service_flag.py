"""Service-level tests for app.services.flag.

The HTTP integration tests in test_phase7.py cover the happy paths via the API.
This file covers the specific service-level error paths that are NOT exercised there:

- create_flag with an invalid target_type (not in TARGET_TABLE_MAP) → BadRequestError
- update_flag with a non-UUID flag_id string → BadRequestError (_to_uuid)
- update_flag called by a non-moderator user → ForbiddenError
- update_flag for a flag that does not exist → NotFoundError
- list_flags with target_type filter returns only matching items
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.flag import Flag, FlagStatus
from app.models.material import Material
from app.models.user import User, UserRole
from app.services.flag import create_flag, list_flags, resolve_flag_targets, update_flag


async def _make_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@test.example",
        display_name="Test",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_material(db: AsyncSession) -> Material:
    m = Material(
        id=uuid.uuid4(),
        title="Test",
        slug=f"test-{uuid.uuid4().hex[:8]}",
        type="document",
        current_version=1,
    )
    db.add(m)
    await db.flush()
    return m


async def _make_flag(
    db: AsyncSession,
    reporter: User,
    target: Material,
    target_type: str = "material",
) -> Flag:
    return await create_flag(
        db,
        reporter_id=reporter.id,
        target_type=target_type,
        target_id=target.id,
        reason="spam",
    )


# ── create_flag ──────────────────────────────────────────────────────────────


async def test_create_flag_invalid_target_type_raises(db_session: AsyncSession) -> None:
    """target_type not in TARGET_TABLE_MAP → BadRequestError."""
    reporter = await _make_user(db_session)
    with pytest.raises(BadRequestError, match="Invalid target_type"):
        await create_flag(
            db_session,
            reporter_id=reporter.id,
            target_type="not_a_real_type",
            target_id=uuid.uuid4(),
            reason="test",
        )


async def test_create_flag_nonexistent_target_raises(db_session: AsyncSession) -> None:
    """Valid target_type but no row with that ID → NotFoundError."""
    reporter = await _make_user(db_session)
    with pytest.raises(NotFoundError):
        await create_flag(
            db_session,
            reporter_id=reporter.id,
            target_type="material",
            target_id=uuid.uuid4(),
            reason="test",
        )


async def test_create_flag_duplicate_raises(db_session: AsyncSession) -> None:
    """Same (reporter, target_type, target_id) twice → BadRequestError."""
    reporter = await _make_user(db_session)
    material = await _make_material(db_session)
    await _make_flag(db_session, reporter, material)

    with pytest.raises(BadRequestError, match="already flagged"):
        await create_flag(
            db_session,
            reporter_id=reporter.id,
            target_type="material",
            target_id=material.id,
            reason="spam",
        )


async def test_create_flag_succeeds(db_session: AsyncSession) -> None:
    reporter = await _make_user(db_session)
    material = await _make_material(db_session)
    flag = await _make_flag(db_session, reporter, material)
    assert flag.id is not None
    assert flag.status == FlagStatus.OPEN
    assert flag.reporter_id == reporter.id


# ── update_flag ──────────────────────────────────────────────────────────────


async def test_update_flag_invalid_uuid_raises(db_session: AsyncSession) -> None:
    """Non-UUID flag_id string → BadRequestError from _to_uuid."""
    moderator = await _make_user(db_session, UserRole.MODERATOR)
    with pytest.raises(BadRequestError, match="Invalid UUID"):
        await update_flag(db_session, "not-a-uuid", moderator, "resolved")


async def test_update_flag_non_moderator_raises(db_session: AsyncSession) -> None:
    """Student trying to update a flag → ForbiddenError."""
    student = await _make_user(db_session, UserRole.STUDENT)
    with pytest.raises(ForbiddenError, match="moderators"):
        await update_flag(db_session, str(uuid.uuid4()), student, "resolved")


async def test_update_flag_not_found_raises(db_session: AsyncSession) -> None:
    """Valid UUID but no matching Flag row → NotFoundError."""
    moderator = await _make_user(db_session, UserRole.MODERATOR)
    with pytest.raises(NotFoundError, match="Flag not found"):
        await update_flag(db_session, str(uuid.uuid4()), moderator, "resolved")


async def test_update_flag_sets_status_and_resolver(db_session: AsyncSession) -> None:
    """update_flag updates status and records the resolver."""
    reporter = await _make_user(db_session)
    moderator = await _make_user(db_session, UserRole.MODERATOR)
    material = await _make_material(db_session)
    flag = await _make_flag(db_session, reporter, material)

    updated = await update_flag(db_session, str(flag.id), moderator, "resolved")
    assert updated.status == FlagStatus.RESOLVED
    assert updated.resolved_by == moderator.id
    assert updated.resolved_at is not None


# ── list_flags ────────────────────────────────────────────────────────────────


async def test_list_flags_target_type_filter(db_session: AsyncSession) -> None:
    """list_flags with target_type only returns flags of that type."""
    reporter = await _make_user(db_session)
    material = await _make_material(db_session)
    await _make_flag(db_session, reporter, material, target_type="material")

    items, total = await list_flags(db_session, limit=10, offset=0, target_type="comment")
    assert total == 0
    assert items == []

    items, total = await list_flags(db_session, limit=10, offset=0, target_type="material")
    assert total == 1
    assert items[0].target_type == "material"


# ── resolve_flag_targets ──────────────────────────────────────────────────────


async def test_resolve_targets_material_preview_and_link(db_session: AsyncSession) -> None:
    """A material flag resolves to the title preview and a root browse link."""
    reporter = await _make_user(db_session)
    material = await _make_material(db_session)
    flag = await _make_flag(db_session, reporter, material)

    targets = await resolve_flag_targets(db_session, [flag])
    info = targets[flag.id]
    assert info.exists is True
    assert info.preview == material.title
    assert info.link == f"/browse/{material.slug}"


async def test_resolve_targets_deleted_content(db_session: AsyncSession) -> None:
    """A flag whose target row is gone resolves to exists=False with no link."""
    reporter = await _make_user(db_session)
    material = await _make_material(db_session)
    flag = await _make_flag(db_session, reporter, material)

    await db_session.delete(material)
    await db_session.flush()

    targets = await resolve_flag_targets(db_session, [flag])
    info = targets[flag.id]
    assert info.exists is False
    assert info.preview is None
    assert info.link is None


async def test_list_flags_status_filter(db_session: AsyncSession) -> None:
    """list_flags with status='open' excludes resolved flags."""
    reporter = await _make_user(db_session)
    moderator = await _make_user(db_session, UserRole.MODERATOR)
    material = await _make_material(db_session)
    flag = await _make_flag(db_session, reporter, material)
    await update_flag(db_session, str(flag.id), moderator, "resolved")

    open_items, open_total = await list_flags(db_session, limit=10, offset=0, status="open")
    assert open_total == 0

    resolved_items, resolved_total = await list_flags(
        db_session, limit=10, offset=0, status="resolved"
    )
    assert resolved_total == 1
    assert resolved_items[0].id == flag.id
