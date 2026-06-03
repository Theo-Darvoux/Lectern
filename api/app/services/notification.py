import logging
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.sse import broadcast_to_user
from app.models.annotation import Annotation
from app.models.comment import Comment
from app.models.material import Material, MaterialFavourite, MaterialLike
from app.models.notification import Notification
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

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
    try:
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
    except Exception:
        logger.exception("SSE broadcast failed for notification %s", notif.id)
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
    return result.rowcount  # type: ignore[attr-defined]


async def notify_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    notification_type: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
) -> None:
    await create_notification(db, user_id, notification_type, title, body, link)


async def notify_material_subscribers(
    db: AsyncSession,
    material_id: uuid.UUID,
    actor_id: uuid.UUID,
    notification_type: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
) -> None:
    """Notify everyone "subscribed" to a material when activity happens on it.

    Subscribers are users who have shown interest in the document: its author,
    anyone who liked or favourited (saved) it, and anyone who previously
    annotated or commented on it. The acting user is always excluded, and the
    recipient set is de-duplicated so each person gets at most one notification.
    """
    recipient_ids: set[uuid.UUID] = set()

    author_res = await db.execute(select(Material.author_id).where(Material.id == material_id))
    author_id = author_res.scalar_one_or_none()
    if author_id:
        recipient_ids.add(author_id)

    like_res = await db.execute(
        select(MaterialLike.user_id).where(MaterialLike.material_id == material_id)
    )
    recipient_ids.update(like_res.scalars().all())

    fav_res = await db.execute(
        select(MaterialFavourite.user_id).where(MaterialFavourite.material_id == material_id)
    )
    recipient_ids.update(fav_res.scalars().all())

    ann_res = await db.execute(
        select(Annotation.author_id).where(Annotation.material_id == material_id)
    )
    recipient_ids.update(a for a in ann_res.scalars().all() if a is not None)

    com_res = await db.execute(
        select(Comment.author_id).where(
            Comment.target_type == "material",
            Comment.target_id == material_id,
        )
    )
    recipient_ids.update(c for c in com_res.scalars().all() if c is not None)

    recipient_ids.discard(actor_id)

    for recipient_id in recipient_ids:
        try:
            await create_notification(db, recipient_id, notification_type, title, body, link)
        except Exception:
            logger.exception(
                "Failed to send %s notification to %s", notification_type, recipient_id
            )


async def notify_admins_pending_user(db: AsyncSession, user: User) -> None:
    """Notify all BUREAU/VIEUX admins when a new user is awaiting approval."""
    result = await db.execute(select(User.id).where(User.role.in_(ADMIN_ROLES)))
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
