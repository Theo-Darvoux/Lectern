import logging
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sse import broadcast_to_user
from app.models.notification import Notification
from app.models.user import User, UserRole

logger = logging.getLogger("wikint")

MODERATOR_ROLES = (UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX)
ADMIN_ROLES = (UserRole.BUREAU, UserRole.VIEUX)


async def create_notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    notification_type: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
) -> Notification:
    notif = Notification(
        user_id=user_id,
        type=notification_type,
        title=title,
        body=body,
        link=link,
    )
    db.add(notif)
    await db.flush()
    broadcast_to_user(
        user_id,
        {
            "type": notification_type,
            "id": str(notif.id),
            "title": title,
            "body": body,
            "link": link,
        },
    )
    return notif


async def get_notifications(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int,
    offset: int,
    read_filter: bool | None = None,
) -> tuple[list[Notification], int]:
    base = select(Notification).where(Notification.user_id == user_id)
    if read_filter is not None:
        base = base.where(Notification.read == read_filter)

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars().all()), total


async def get_unread_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id, Notification.read.is_(False)
        )
    )
    return result.scalar_one()


async def mark_read(db: AsyncSession, notification_id: str, user_id: uuid.UUID) -> Notification:
    nid = uuid.UUID(str(notification_id))
    result = await db.execute(
        select(Notification).where(Notification.id == nid, Notification.user_id == user_id)
    )
    notif = result.scalar_one_or_none()
    if not notif:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Notification not found")
    notif.read = True
    await db.flush()
    return notif


async def mark_all_read(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read.is_(False))
        .values(read=True)
    )
    await db.flush()
    from typing import Any, cast

    return cast(Any, result).rowcount  # type: ignore[no-any-return]


async def notify_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    notification_type: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
) -> None:
    await create_notification(db, user_id, notification_type, title, body, link)


async def notify_admins_pending_user(db: AsyncSession, user: "User") -> None:  # type: ignore[name-defined]
    """Notify all BUREAU/VIEUX admins when a new user is awaiting approval."""
    from app.models.user import User as UserModel

    result = await db.execute(select(UserModel.id).where(UserModel.role.in_(ADMIN_ROLES)))
    admin_ids = list(result.scalars().all())
    notifications = [
        Notification(
            user_id=aid,
            type="pending_user",
            title="New user pending approval",
            body=f"{user.email} is requesting access.",
            link="/admin/users?role=pending",
        )
        for aid in admin_ids
    ]
    if notifications:
        db.add_all(notifications)
        await db.flush()
        for notif in notifications:
            broadcast_to_user(
                notif.user_id,
                {
                    "type": "pending_user",
                    "id": str(notif.id),
                    "title": notif.title,
                    "body": notif.body,
                    "link": notif.link,
                },
            )


async def notify_moderators(
    db: AsyncSession,
    notification_type: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
) -> None:
    result = await db.execute(
        select(User.id).where(
            User.role.in_(MODERATOR_ROLES),
        )
    )
    mod_ids = list(result.scalars().all())
    notifications = [
        Notification(
            user_id=mid,
            type=notification_type,
            title=title,
            body=body,
            link=link,
        )
        for mid in mod_ids
    ]
    if notifications:
        db.add_all(notifications)
        await db.flush()
        for notif in notifications:
            broadcast_to_user(
                notif.user_id,
                {
                    "type": notification_type,
                    "id": str(notif.id),
                    "title": title,
                    "body": body,
                    "link": link,
                },
            )
