import uuid

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag, directory_tags, material_tags


async def prune_orphan_tags(db: AsyncSession) -> None:
    """Delete tags no longer referenced by any material or directory."""
    orphan_ids = list(
        await db.scalars(
            select(Tag.id).where(
                ~exists().where(material_tags.c.tag_id == Tag.id),
                ~exists().where(directory_tags.c.tag_id == Tag.id),
            )
        )
    )
    if orphan_ids:
        await db.execute(delete(Tag).where(Tag.id.in_(orphan_ids)))


async def get_or_create_tags(db: AsyncSession, tag_names: list[str]) -> list[uuid.UUID]:
    if not tag_names:
        return []

    normalized_names = [t.strip().lower() for t in tag_names if t.strip()]
    if not normalized_names:
        return []

    # Find existing tags
    stmt = select(Tag).where(Tag.name.in_(normalized_names))
    result = await db.execute(stmt)
    existing_tags = result.scalars().all()

    existing_names = {t.name for t in existing_tags}
    new_names = set(normalized_names) - existing_names

    new_tags = []
    for name in new_names:
        tag = Tag(id=uuid.uuid4(), name=name)
        db.add(tag)
        new_tags.append(tag)

    if new_tags:
        await db.flush()

    all_tag_ids = [t.id for t in existing_tags] + [t.id for t in new_tags]
    return all_tag_ids
