import json
import typing
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.models.material import Material, MaterialFavourite, MaterialLike, MaterialVersion
from app.models.view_history import ViewHistory


def material_orm_to_dict(
    m: Material,
    *,
    attachment_count: int = 0,
    comment_count: int = 0,
    annotation_count: int = 0,
    directory_path: str | None = None,
    current_user_id: uuid.UUID | None = None,
    is_liked: bool | None = None,
    is_favourited: bool | None = None,
) -> dict[str, typing.Any]:
    """Convert a Material ORM instance to a plain dict safe for Pydantic validation.

    This avoids MissingGreenlet errors caused by SQLAlchemy lazy-loading
    relationship attributes when Pydantic inspects the object with
    ``from_attributes=True``.

    ``is_liked`` / ``is_favourited`` may be supplied directly by callers that
    have already resolved membership in a batched query (avoids loading the
    full ``likes`` / ``favourites`` collections per material). When left as
    ``None`` they fall back to inspecting eagerly-loaded relationships.
    """
    path = directory_path
    if not path and "directory" in m.__dict__:
        path = m.directory.slug

    # Determine if current user liked/favourited this. Prefer explicitly
    # provided (batched) values; otherwise derive from loaded relationships.
    if is_liked is None and current_user_id and "likes" in m.__dict__:
        is_liked = any(like.user_id == current_user_id for like in m.likes)
    if is_favourited is None and current_user_id and "favourites" in m.__dict__:
        is_favourited = any(fav.user_id == current_user_id for fav in m.favourites)
    is_liked = bool(is_liked)
    is_favourited = bool(is_favourited)

    return {
        "id": m.id,
        "directory_id": m.directory_id,
        "directory_path": path,
        "title": m.title,
        "slug": m.slug,
        "description": m.description,
        "type": m.type,
        "current_version": m.current_version,
        "parent_material_id": m.parent_material_id,
        "author_id": m.author_id,
        "metadata": m.metadata_,
        "download_count": m.download_count,
        "total_views": m.total_views,
        "views_today": m.views_today,
        "like_count": m.like_count,
        "is_liked": is_liked,
        "is_favourited": is_favourited,
        "tags": [t.name for t in m.tags] if "tags" in m.__dict__ else [],
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "attachment_count": attachment_count,
        "comment_count": comment_count,
        "annotation_count": annotation_count,
        "current_version_info": None,
    }


def version_orm_to_dict(v: MaterialVersion) -> dict[str, typing.Any]:
    """Convert a MaterialVersion ORM instance to a plain dict safe for Pydantic validation."""
    return {
        "id": v.id,
        "material_id": v.material_id,
        "version_number": v.version_number,
        "file_key": v.file_key,
        "file_name": v.file_name,
        "file_size": v.file_size,
        "file_mime_type": v.file_mime_type,
        "diff_summary": v.diff_summary,
        "author_id": v.author_id,
        "pr_id": v.pr_id,
        "virus_scan_result": v.virus_scan_result.value
        if hasattr(v.virus_scan_result, "value")
        else v.virus_scan_result,
        "version_lock": v.version_lock,
        "created_at": v.created_at,
        "thumbnail_key": v.thumbnail_key,
    }


async def get_material_thumbnail_info(
    db: AsyncSession,
    material_id: uuid.UUID,
    redis: typing.Any = None,
) -> dict[str, typing.Any] | None:
    """Single JOIN query returning only the fields needed to serve a thumbnail.

    Results are cached in Redis for 120 s to avoid hitting the DB on every
    card render when a directory listing has many materials.
    """
    cache_key = f"thumbnail:v1:{material_id}"

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    result = await db.execute(
        select(
            MaterialVersion.thumbnail_key,
            MaterialVersion.file_key,
            MaterialVersion.file_mime_type,
            MaterialVersion.file_name,
        )
        .join(Material, Material.id == MaterialVersion.material_id)
        .where(
            Material.id == material_id,
            MaterialVersion.version_number == Material.current_version,
        )
    )
    row = result.one_or_none()
    if not row:
        return None

    data: dict[str, typing.Any] = {
        "thumbnail_key": row.thumbnail_key,
        "file_key": row.file_key,
        "file_mime_type": row.file_mime_type,
        "file_name": row.file_name,
    }

    if redis is not None:
        try:
            await redis.set(cache_key, json.dumps(data, default=str), ex=120)
        except Exception:
            pass

    return data


async def get_material_by_id(db: AsyncSession, material_id: str | uuid.UUID) -> Material:

    if isinstance(material_id, str):
        import uuid

        material_id = uuid.UUID(material_id)
    result = await db.execute(
        select(Material).options(selectinload(Material.tags)).where(Material.id == material_id)
    )
    material = result.scalar_one_or_none()
    if not material:
        raise NotFoundError("Material not found")
    return material


def check_material_access(user_id: uuid.UUID, material: dict[str, typing.Any]) -> None:
    """
    Authorization choke-point for material access.

    SECURITY: This function is currently a stub — it always permits access.
    Any authenticated user can reach any material regardless of ownership or
    future visibility/ACL fields.  When per-material access controls are
    introduced (e.g. a `visibility` or `published` flag, course enrollment
    checks, etc.) they MUST be enforced here so all call sites are covered.

    Do NOT add inline access checks at call sites; route them through this
    function instead.
    """
    # TODO: enforce material["visibility"], enrollment membership, etc. once
    # those fields exist on the Material model.
    _ = user_id, material  # suppress unused-variable warnings until implemented


async def get_material_file_info(db: AsyncSession, material_id: str | uuid.UUID) -> MaterialVersion:
    """Single JOIN query returning only the fields needed to serve a file."""
    if isinstance(material_id, str):
        material_id = uuid.UUID(material_id)
    result = await db.execute(
        select(MaterialVersion)
        .join(Material, Material.id == MaterialVersion.material_id)
        .where(
            Material.id == material_id,
            MaterialVersion.version_number == Material.current_version,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise NotFoundError("No file available")
    return version


async def get_material_with_version(
    db: AsyncSession, material_id: str | uuid.UUID, current_user_id: uuid.UUID | None = None
) -> dict[str, typing.Any]:

    if isinstance(material_id, str):
        import uuid

        material_id = uuid.UUID(material_id)
    material = await get_material_by_id(db, material_id)

    version_result = await db.execute(
        select(MaterialVersion).where(
            MaterialVersion.material_id == material.id,
            MaterialVersion.version_number == material.current_version,
        )
    )
    current_version = version_result.scalar_one_or_none()

    # Count attachments (child materials)
    att_count = (
        await db.scalar(
            select(func.count())
            .select_from(Material)
            .where(Material.parent_material_id == material.id)
        )
        or 0
    )

    # Count comments keyed to this material
    from app.models.comment import Comment

    com_count = (
        await db.scalar(
            select(func.count())
            .select_from(Comment)
            .where(Comment.target_type == "material", Comment.target_id == material.id)
        )
        or 0
    )

    # Count root-level annotations on this material
    from app.models.annotation import Annotation

    ann_count = (
        await db.scalar(
            select(func.count())
            .select_from(Annotation)
            .where(Annotation.material_id == material.id)
        )
        or 0
    )

    mat_dict = material_orm_to_dict(
        material,
        attachment_count=att_count,
        comment_count=com_count,
        annotation_count=ann_count,
        current_user_id=current_user_id,
    )
    if current_version:
        mat_dict["current_version_info"] = version_orm_to_dict(current_version)
    return mat_dict


async def get_material_versions(
    db: AsyncSession, material_id: str | uuid.UUID
) -> list[MaterialVersion]:

    if isinstance(material_id, str):
        import uuid

        material_id = uuid.UUID(material_id)
    await get_material_by_id(db, material_id)
    result = await db.execute(
        select(MaterialVersion)
        .where(MaterialVersion.material_id == material_id)
        .order_by(MaterialVersion.version_number.desc())
    )
    return list(result.scalars().all())


async def get_material_version(
    db: AsyncSession, material_id: str, version_number: int
) -> MaterialVersion:
    import uuid as _uuid

    uid = _uuid.UUID(str(material_id))
    await get_material_by_id(db, material_id)
    result = await db.execute(
        select(MaterialVersion).where(
            MaterialVersion.material_id == uid,
            MaterialVersion.version_number == version_number,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise NotFoundError(f"Version {version_number} not found")
    return version


async def get_material_attachments(
    db: AsyncSession, material_id: str | uuid.UUID, current_user_id: uuid.UUID | None = None
) -> list[dict[str, typing.Any]]:

    if isinstance(material_id, str):
        import uuid

        material_id = uuid.UUID(material_id)
    await get_material_by_id(db, material_id)
    result = await db.execute(
        select(Material, MaterialVersion)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(Material.parent_material_id == material_id)
        .order_by(Material.title)
    )

    attachments_out = []
    for material, version in result.all():
        mat_dict = material_orm_to_dict(material, current_user_id=current_user_id)
        if version:
            mat_dict["current_version_info"] = version_orm_to_dict(version)
        attachments_out.append(mat_dict)
    return attachments_out


async def increment_download_count(db: AsyncSession, material_id: str | uuid.UUID) -> Material:

    if isinstance(material_id, str):
        import uuid

        material_id = uuid.UUID(material_id)
    material = await get_material_by_id(db, material_id)
    material.download_count += 1
    await db.flush()
    return material


async def record_view(db: AsyncSession, user_id: str, material_id: str) -> None:
    from sqlalchemy import update
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    uid = uuid.UUID(str(user_id))
    mid = uuid.UUID(str(material_id))

    # Ensure material exists (raises NotFoundError if it doesn't)
    await get_material_by_id(db, mid)

    # 1. Update counters on the Material itself (Atomic increment in SQL)
    await db.execute(
        update(Material)
        .where(Material.id == mid)
        .values(
            total_views=Material.total_views + 1,
            views_today=Material.views_today + 1,
        )
    )

    # 2. Record individual view in ViewHistory (Last viewed by this user)
    stmt = pg_insert(ViewHistory).values(
        id=uuid.uuid4(),
        user_id=uid,
        material_id=mid,
        viewed_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_view_history_user_material",
        set_={"viewed_at": stmt.excluded.viewed_at},
    )
    await db.execute(stmt)

    # 3. Best practice: Also increment in Redis for ultra-fast access if needed
    # (Though for now we primarily read from DB, Redis can serve as a hot cache)
    try:
        from app.core.redis import redis_client

        # We use a hash for all material totals to keep it clean
        await redis_client.hincrby("material:views:total", str(mid), 1)  # type: ignore[misc]
        # For "today", we use a daily key that can be easily expired
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        daily_key = f"material:views:today:{today}"
        await redis_client.hincrby(daily_key, str(mid), 1)  # type: ignore[misc]
        await redis_client.expire(daily_key, 86400 * 2)  # Keep for 2 days just in case
    except Exception:
        # Don't fail the request if Redis is down
        pass

    await db.flush()


async def toggle_like(db: AsyncSession, user_id: uuid.UUID, material_id: uuid.UUID) -> bool:
    """Toggle a like for a material. Returns True if liked, False if unliked."""
    # Check if exists
    result = await db.execute(
        select(MaterialLike).where(
            MaterialLike.user_id == user_id, MaterialLike.material_id == material_id
        )
    )
    like = result.scalar_one_or_none()

    if like:
        # Unlike
        await db.delete(like)
        await db.execute(
            update(Material)
            .where(Material.id == material_id)
            .values(like_count=Material.like_count - 1)
        )
        liked = False
    else:
        # Like
        new_like = MaterialLike(id=uuid.uuid4(), user_id=user_id, material_id=material_id)
        db.add(new_like)
        await db.execute(
            update(Material)
            .where(Material.id == material_id)
            .values(like_count=Material.like_count + 1)
        )
        liked = True

    await db.flush()
    return liked


async def toggle_favourite(db: AsyncSession, user_id: uuid.UUID, material_id: uuid.UUID) -> bool:
    """Toggle a favourite for a material. Returns True if favourited, False if removed."""
    # Check if exists
    result = await db.execute(
        select(MaterialFavourite).where(
            MaterialFavourite.user_id == user_id, MaterialFavourite.material_id == material_id
        )
    )
    favourite = result.scalar_one_or_none()

    if favourite:
        # Remove favourite
        await db.delete(favourite)
        favourited = False
    else:
        # Add favourite
        new_favourite = MaterialFavourite(id=uuid.uuid4(), user_id=user_id, material_id=material_id)
        db.add(new_favourite)
        favourited = True

    await db.flush()
    return favourited
