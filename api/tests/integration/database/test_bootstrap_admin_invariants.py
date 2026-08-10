from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.common.exceptions import ConflictError, ForbiddenError
from app.models.installation import InstallationState
from app.models.user import User, UserRole
from app.services.auth import (
    acquire_setup_lock,
    ensure_admin_removal_safe,
    installation_bootstrapped,
    mark_installation_bootstrapped,
)

DATABASE_URL = os.environ.get("REVERT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="REVERT_TEST_DATABASE_URL is not configured"),
]


@pytest.mark.asyncio
async def test_two_concurrent_final_admin_removals_cannot_create_zero_admin_state() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex

    async with sessions() as seed:
        # This CI database is shared by the selected integration modules. Remove
        # prior administrative fixtures so this test exercises the true final-admin boundary.
        await seed.execute(delete(InstallationState).where(InstallationState.id == 1))
        await seed.execute(
            update(User)
            .where(User.role.in_([UserRole.BUREAU, UserRole.VIEUX]))
            .values(role=UserRole.STUDENT)
        )
        await seed.commit()

        admins = [
            User(email=f"admin-a-{suffix}@example.invalid", role=UserRole.BUREAU),
            User(email=f"admin-b-{suffix}@example.invalid", role=UserRole.VIEUX),
        ]
        seed.add_all(admins)
        await seed.flush()
        await mark_installation_bootstrapped(seed)
        await seed.commit()
        first_id, second_id = admins[0].id, admins[1].id

    async def demote(user_id: uuid.UUID) -> str:
        async with sessions() as session:
            user = await session.get(User, user_id)
            assert user is not None
            try:
                await ensure_admin_removal_safe(session, user.id)
                user.role = UserRole.STUDENT
                await session.commit()
                return "demoted"
            except ConflictError:
                await session.rollback()
                return "blocked"

    results = await asyncio.gather(demote(first_id), demote(second_id))
    assert sorted(results) == ["blocked", "demoted"]

    async with sessions() as check:
        live_admins = list(
            (
                await check.scalars(
                    select(User).where(
                        User.id.in_([first_id, second_id]),
                        User.role.in_([UserRole.BUREAU, UserRole.VIEUX]),
                    )
                )
            ).all()
        )
        assert len(live_admins) == 1
        assert await installation_bootstrapped(check) is True

        await check.execute(delete(User).where(User.id.in_([first_id, second_id])))
        await check.execute(delete(InstallationState).where(InstallationState.id == 1))
        await check.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_student_delete_cannot_remove_newly_promoted_final_admin() -> None:
    """Regression for a pre-lock role TOCTOU in hard deletion.

    The deleting session intentionally loads B while B is a student. Another
    transaction then promotes B, and A is demoted so B becomes the final admin.
    The stale deletion must re-read B under the shared authority lock and fail.
    """
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex

    async with sessions() as seed:
        await seed.execute(delete(InstallationState).where(InstallationState.id == 1))
        await seed.execute(
            update(User)
            .where(User.role.in_([UserRole.BUREAU, UserRole.VIEUX]))
            .values(role=UserRole.STUDENT)
        )
        admin_a = User(email=f"stale-a-{suffix}@example.invalid", role=UserRole.BUREAU)
        user_b = User(email=f"stale-b-{suffix}@example.invalid", role=UserRole.STUDENT)
        seed.add_all([admin_a, user_b])
        await seed.flush()
        await mark_installation_bootstrapped(seed)
        await seed.commit()
        admin_a_id, user_b_id = admin_a.id, user_b.id

    from app.services.auth import lock_user_for_authority_change
    from app.services.user import hard_delete_user

    async with sessions() as stale_delete:
        stale_b = await stale_delete.get(User, user_b_id)
        assert stale_b is not None
        assert stale_b.role == UserRole.STUDENT

        async with sessions() as promote:
            current_b = await lock_user_for_authority_change(promote, user_b_id)
            assert current_b is not None
            assert current_b.role == UserRole.STUDENT
            current_b.role = UserRole.BUREAU
            await promote.commit()

        async with sessions() as demote_a:
            current_a = await lock_user_for_authority_change(demote_a, admin_a_id)
            assert current_a is not None
            await ensure_admin_removal_safe(demote_a, current_a.id)
            current_a.role = UserRole.STUDENT
            await demote_a.commit()

        # stale_b still says STUDENT in this session. hard_delete_user must not trust it.
        assert stale_b.role == UserRole.STUDENT
        with pytest.raises(ConflictError, match="final administrator"):
            await hard_delete_user(stale_delete, stale_b)
        await stale_delete.rollback()

    async with sessions() as check:
        live_admin_ids = set(
            await check.scalars(
                select(User.id).where(
                    User.id.in_([admin_a_id, user_b_id]),
                    User.role.in_([UserRole.BUREAU, UserRole.VIEUX]),
                    User.deleted_at.is_(None),
                )
            )
        )
        assert live_admin_ids == {user_b_id}

        await check.execute(delete(User).where(User.id.in_([admin_a_id, user_b_id])))
        await check.execute(delete(InstallationState).where(InstallationState.id == 1))
        await check.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_marker_and_admin_mutations_share_one_lock() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as first, sessions() as second:
        await acquire_setup_lock(first)
        waiter = asyncio.create_task(acquire_setup_lock(second))
        await asyncio.sleep(0.2)
        assert not waiter.done(), "shared admin-authority lock did not serialize transactions"
        await first.rollback()
        await asyncio.wait_for(waiter, timeout=5)
        await second.rollback()

    await engine.dispose()


@pytest.mark.asyncio
async def test_demoted_admin_waiting_on_authority_lock_cannot_promote_replacement() -> None:
    """Revocation wins over a request admitted before the authority lock.

    T1 has already loaded A as BUREAU (standing in for AdminUser dependency success).
    T2 holds the shared lock while demoting A. T1 then queues a promotion and must
    block. After T2 commits, T1 acquires the lock, freshly revalidates A, and fails.
    """
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex

    async with sessions() as seed:
        await seed.execute(delete(InstallationState).where(InstallationState.id == 1))
        await seed.execute(
            update(User)
            .where(User.role.in_([UserRole.BUREAU, UserRole.VIEUX]))
            .values(role=UserRole.STUDENT)
        )
        admin_a = User(email=f"actor-a-{suffix}@example.invalid", role=UserRole.BUREAU)
        admin_b = User(email=f"actor-b-{suffix}@example.invalid", role=UserRole.BUREAU)
        target_c = User(email=f"actor-c-{suffix}@example.invalid", role=UserRole.STUDENT)
        seed.add_all([admin_a, admin_b, target_c])
        await seed.flush()
        await mark_installation_bootstrapped(seed)
        await seed.commit()
        admin_a_id, admin_b_id, target_c_id = admin_a.id, admin_b.id, target_c.id

    from app.routers.admin import admin_update_role
    from app.services.auth import lock_admin_authority_change

    async with sessions() as stale_request, sessions() as revoker:
        stale_a = await stale_request.get(User, admin_a_id)
        assert stale_a is not None
        assert stale_a.role == UserRole.BUREAU
        admitted_generation = stale_a.auth_generation

        current_b, current_a = await lock_admin_authority_change(
            revoker,
            admin_b_id,
            admin_a_id,
            expected_auth_generation=0,
        )
        assert current_b.role == UserRole.BUREAU
        assert current_a is not None
        current_a.role = UserRole.STUDENT
        await revoker.flush()

        queued = asyncio.create_task(
            admin_update_role(
                user_id=target_c_id,
                _user=stale_a,
                db=stale_request,
                role=UserRole.BUREAU.value,
            )
        )
        await asyncio.sleep(0.2)
        assert not queued.done(), "pre-authorized request did not wait for authority lock"

        await revoker.commit()
        with pytest.raises(ForbiddenError, match="authority was revoked"):
            await asyncio.wait_for(queued, timeout=5)
        await stale_request.rollback()
        assert admitted_generation == 0

    async with sessions() as check:
        current_a = await check.get(User, admin_a_id)
        current_b = await check.get(User, admin_b_id)
        current_c = await check.get(User, target_c_id)
        assert current_a is not None and current_a.role == UserRole.STUDENT
        assert current_b is not None and current_b.role == UserRole.BUREAU
        assert current_c is not None and current_c.role == UserRole.STUDENT

        await check.execute(delete(User).where(User.id.in_([admin_a_id, admin_b_id, target_c_id])))
        await check.execute(delete(InstallationState).where(InstallationState.id == 1))
        await check.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_pending_reject_cannot_delete_user_approved_while_request_is_stale() -> None:
    """Reject must re-read PENDING state under the same authority serialization boundary."""
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex

    async with sessions() as seed:
        admin = User(email=f"reject-admin-{suffix}@example.invalid", role=UserRole.BUREAU)
        pending = User(email=f"reject-pending-{suffix}@example.invalid", role=UserRole.PENDING)
        seed.add_all([admin, pending])
        await seed.commit()
        admin_id, pending_id = admin.id, pending.id

    from app.core.common.exceptions import BadRequestError
    from app.routers.admin import admin_reject_user
    from app.services.auth import lock_admin_authority_change

    async with sessions() as stale_reject:
        stale_admin = await stale_reject.get(User, admin_id)
        stale_pending = await stale_reject.get(User, pending_id)
        assert stale_admin is not None and stale_admin.role == UserRole.BUREAU
        assert stale_pending is not None and stale_pending.role == UserRole.PENDING

        async with sessions() as approve:
            _, current_pending = await lock_admin_authority_change(
                approve,
                admin_id,
                pending_id,
                expected_auth_generation=0,
            )
            assert current_pending is not None
            current_pending.role = UserRole.STUDENT
            await approve.commit()

        # Both ORM instances in stale_reject predate the approval. The route must
        # refresh the target under the authority lock before checking PENDING.
        assert stale_pending.role == UserRole.PENDING
        with pytest.raises(BadRequestError, match="not pending approval"):
            await admin_reject_user(
                user_id=pending_id,
                _user=stale_admin,
                db=stale_reject,
                reason=None,
            )
        await stale_reject.rollback()

    async with sessions() as check:
        current = await check.get(User, pending_id)
        assert current is not None
        assert current.role == UserRole.STUDENT
        await check.execute(delete(User).where(User.id.in_([admin_id, pending_id])))
        await check.commit()

    await engine.dispose()
