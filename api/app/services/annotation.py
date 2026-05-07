import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.annotation import Annotation
from app.models.material import Material, MaterialVersion
from app.models.user import User, UserRole


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


async def _get_material_current_version(
    db: AsyncSession, material_id: uuid.UUID
) -> MaterialVersion:
    result = await db.execute(select(Material).where(Material.id == material_id))
    material = result.scalar_one_or_none()
    if not material:
        raise NotFoundError("Material not found")

    ver_result = await db.execute(
        select(MaterialVersion).where(
            MaterialVersion.material_id == material_id,
            MaterialVersion.version_number == material.current_version,
        )
    )
    version = ver_result.scalar_one_or_none()
    if not version:
        raise NotFoundError("Current version not found")
    return version


async def get_annotations(
    db: AsyncSession,
    material_id: str,
    limit: int,
    offset: int,
    version: int | None = None,
    doc_page: int | None = None,
) -> tuple[list[Annotation], int]:
    mid = _to_uuid(material_id)

    mat_res = await db.execute(select(Material).where(Material.id == mid))
    if not mat_res.scalar_one_or_none():
        raise NotFoundError("Material not found")

    base = select(Annotation).where(
        Annotation.material_id == mid,
        Annotation.thread_id == Annotation.id,
    )

    if version is not None:
        ver_result = await db.execute(
            select(MaterialVersion).where(
                MaterialVersion.material_id == mid,
                MaterialVersion.version_number == version,
            )
        )
        ver = ver_result.scalar_one_or_none()
        if ver:
            base = base.where(Annotation.version_id == ver.id)

    if doc_page is not None:
        base = base.where(Annotation.page == doc_page)

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    ann_result = await db.execute(
        base.options(joinedload(Annotation.author))
        .order_by(Annotation.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    root_annotations = list(ann_result.scalars().unique().all())

    if root_annotations:
        thread_ids = [a.id for a in root_annotations]
        replies_result = await db.execute(
            select(Annotation)
            .options(joinedload(Annotation.author))
            .where(
                Annotation.thread_id.in_(thread_ids),
                Annotation.id != Annotation.thread_id,
            )
            .order_by(Annotation.created_at.asc())
        )
        replies = list(replies_result.scalars().unique().all())

        reply_map: dict[uuid.UUID, list[Annotation]] = {}
        for reply in replies:
            if reply.thread_id:
                reply_map.setdefault(reply.thread_id, []).append(reply)

        for root in root_annotations:
            root._replies = reply_map.get(root.id, [])
    else:
        for root in root_annotations:
            root._replies = []

    return root_annotations, total


async def create_annotation(
    db: AsyncSession,
    material_id: str,
    author_id: uuid.UUID,
    body: str,
    selection_text: str | None = None,
    position_data: dict | None = None,  # type: ignore[type-arg]
    page: int | None = None,
    reply_to_id: str | None = None,
) -> Annotation:
    mid = _to_uuid(material_id)
    version = await _get_material_current_version(db, mid)

    if reply_to_id:
        rtid = _to_uuid(reply_to_id)
        rt_result = await db.execute(select(Annotation).where(Annotation.id == rtid))
        reply_target = rt_result.scalar_one_or_none()
        if not reply_target:
            raise NotFoundError("Annotation to reply to not found")
        if reply_target.material_id != mid:
            raise BadRequestError("Reply target belongs to a different material")

        thread_id = reply_target.thread_id if reply_target.thread_id else reply_target.id

        annotation = Annotation(
            material_id=mid,
            version_id=version.id,
            author_id=author_id,
            body=body,
            thread_id=thread_id,
            reply_to_id=rtid,
        )
    else:
        if not position_data:
            raise BadRequestError("position_data is required for root annotations")

        annotation = Annotation(
            material_id=mid,
            version_id=version.id,
            author_id=author_id,
            body=body,
            page=page,
            selection_text=selection_text,
            position_data=position_data,
        )

    db.add(annotation)
    await db.flush()

    if not reply_to_id:
        annotation.thread_id = annotation.id
        await db.flush()

    if (
        reply_to_id
        and reply_target
        and reply_target.author_id
        and reply_target.author_id != author_id
    ):
        from app.services.notification import notify_user

        await notify_user(
            db,
            reply_target.author_id,
            "annotation_reply",
            "Someone replied to your annotation",
            link=f"/browse?material={material_id}",
        )

    result = await db.execute(
        select(Annotation)
        .options(joinedload(Annotation.author))
        .where(Annotation.id == annotation.id)
    )
    return result.scalar_one()


async def update_annotation(
    db: AsyncSession,
    annotation_id: str,
    user: User,
    body: str,
) -> Annotation:
    aid = _to_uuid(annotation_id)
    result = await db.execute(
        select(Annotation).options(joinedload(Annotation.author)).where(Annotation.id == aid)
    )
    annotation = result.scalar_one_or_none()
    if not annotation:
        raise NotFoundError("Annotation not found")

    if annotation.author_id != user.id:
        raise ForbiddenError("Only the author can edit this annotation")

    annotation.body = body
    annotation.updated_at = datetime.now(UTC)
    await db.flush()
    return annotation


async def delete_annotation(
    db: AsyncSession,
    annotation_id: str,
    user: User,
) -> Annotation:
    aid = _to_uuid(annotation_id)
    result = await db.execute(select(Annotation).where(Annotation.id == aid))
    annotation = result.scalar_one_or_none()
    if not annotation:
        raise NotFoundError("Annotation not found")

    is_moderator = user.role in (UserRole.MODERATOR, UserRole.BUREAU, UserRole.VIEUX)
    if annotation.author_id != user.id and not is_moderator:
        raise ForbiddenError("Only the author or a moderator can delete this annotation")

    is_thread_root = annotation.thread_id == annotation.id

    if is_thread_root:
        replies_result = await db.execute(
            select(Annotation).where(
                Annotation.thread_id == aid,
                Annotation.id != aid,
            )
        )
        for reply in replies_result.scalars().all():
            await db.delete(reply)
        await db.flush()

        annotation.thread_id = None
        await db.flush()

    if annotation.reply_to_id == annotation.id:
        annotation.reply_to_id = None
        await db.flush()

    await db.delete(annotation)
    await db.flush()
    return annotation
