"""
Service-level tests for notification fanout functions:
  notify_material_subscribers, notify_moderators, notify_admins_pending_user.
Tests verify subscriber collection, deduplication, and role targeting.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory
from app.models.material import Material, MaterialFavourite, MaterialLike, MaterialVersion
from app.models.notification import Notification
from app.models.user import User, UserRole
from app.services.notification import (
    notify_admins_pending_user,
    notify_material_subscribers,
    notify_moderators,
)


async def _make_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@test.example",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_material(db: AsyncSession, author: User) -> Material:
    directory = Directory(
        id=uuid.uuid4(),
        name="Dir",
        slug=f"dir-{uuid.uuid4().hex[:6]}",
        type="folder",
        created_by=author.id,
    )
    db.add(directory)
    await db.flush()
    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Doc",
        slug=f"doc-{uuid.uuid4().hex[:6]}",
        type="document",
        current_version=1,
        author_id=author.id,
    )
    db.add(material)
    await db.flush()
    db.add(
        MaterialVersion(
            id=uuid.uuid4(),
            material_id=material.id,
            version_number=1,
            file_key="k.pdf",
            file_name="k.pdf",
            file_size=1,
            file_mime_type="application/pdf",
        )
    )
    await db.flush()
    return material


async def _notif_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    rows = (
        (await db.execute(select(Notification).where(Notification.user_id == user_id)))
        .scalars()
        .all()
    )
    return len(rows)


async def test_notify_material_subscribers_notifies_author(db_session: AsyncSession) -> None:
    author = await _make_user(db_session)
    actor = await _make_user(db_session)
    material = await _make_material(db_session, author)

    await notify_material_subscribers(
        db_session, material.id, actor.id, "material_comment", "A comment was posted"
    )
    await db_session.flush()

    notifs = (
        (await db_session.execute(select(Notification).where(Notification.user_id == author.id)))
        .scalars()
        .all()
    )
    assert len(notifs) == 1
    assert notifs[0].type == "material_comment"


async def test_notify_material_subscribers_excludes_actor(db_session: AsyncSession) -> None:
    actor = await _make_user(db_session)
    material = await _make_material(db_session, actor)  # actor is also the author

    await notify_material_subscribers(
        db_session, material.id, actor.id, "material_comment", "Self-comment"
    )
    await db_session.flush()

    assert await _notif_count(db_session, actor.id) == 0


async def test_notify_material_subscribers_deduplicates_author_who_liked(
    db_session: AsyncSession,
) -> None:
    author = await _make_user(db_session)
    actor = await _make_user(db_session)
    material = await _make_material(db_session, author)
    db_session.add(MaterialLike(user_id=author.id, material_id=material.id))
    await db_session.flush()

    await notify_material_subscribers(
        db_session, material.id, actor.id, "material_comment", "Dup test"
    )
    await db_session.flush()

    assert await _notif_count(db_session, author.id) == 1


async def test_notify_material_subscribers_reaches_likers_and_favourites(
    db_session: AsyncSession,
) -> None:
    author = await _make_user(db_session)
    liker = await _make_user(db_session)
    favouriter = await _make_user(db_session)
    actor = await _make_user(db_session)
    material = await _make_material(db_session, author)

    db_session.add(MaterialLike(user_id=liker.id, material_id=material.id))
    db_session.add(MaterialFavourite(user_id=favouriter.id, material_id=material.id))
    await db_session.flush()

    await notify_material_subscribers(db_session, material.id, actor.id, "test_type", "msg")
    await db_session.flush()

    for subscriber in (author, liker, favouriter):
        assert await _notif_count(db_session, subscriber.id) == 1, (
            f"Expected 1 notification for {subscriber.email}"
        )
    assert await _notif_count(db_session, actor.id) == 0


async def test_notify_moderators_targets_all_moderator_roles(db_session: AsyncSession) -> None:
    mod = await _make_user(db_session, UserRole.MODERATOR)
    bureau = await _make_user(db_session, UserRole.BUREAU)
    vieux = await _make_user(db_session, UserRole.VIEUX)
    student = await _make_user(db_session, UserRole.STUDENT)

    await notify_moderators(db_session, "flag_created", "New flag", "Something was flagged")
    await db_session.flush()

    for privileged in (mod, bureau, vieux):
        notifs = (
            (
                await db_session.execute(
                    select(Notification).where(Notification.user_id == privileged.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(notifs) == 1
        assert notifs[0].type == "flag_created"

    assert await _notif_count(db_session, student.id) == 0


async def test_notify_moderators_noop_when_no_moderators(db_session: AsyncSession) -> None:
    student = await _make_user(db_session, UserRole.STUDENT)
    await notify_moderators(db_session, "flag_created", "title")
    await db_session.flush()
    assert await _notif_count(db_session, student.id) == 0


async def test_notify_admins_pending_user_targets_bureau_and_vieux_only(
    db_session: AsyncSession,
) -> None:
    bureau = await _make_user(db_session, UserRole.BUREAU)
    vieux = await _make_user(db_session, UserRole.VIEUX)
    mod = await _make_user(db_session, UserRole.MODERATOR)
    pending = await _make_user(db_session, UserRole.PENDING)

    await notify_admins_pending_user(db_session, pending)
    await db_session.flush()

    for admin in (bureau, vieux):
        notifs = (
            (await db_session.execute(select(Notification).where(Notification.user_id == admin.id)))
            .scalars()
            .all()
        )
        assert len(notifs) == 1
        assert notifs[0].type == "pending_user"
        assert pending.email in (notifs[0].body or "")

    for non_admin in (mod, pending):
        assert await _notif_count(db_session, non_admin.id) == 0
