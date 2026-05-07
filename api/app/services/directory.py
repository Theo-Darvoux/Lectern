import re
import typing
import unicodedata
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.models.directory import Directory, DirectoryFavourite, DirectoryLike
from app.models.material import Material, MaterialVersion


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")


async def get_directory_paths(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not directory_ids:
        return {}

    from sqlalchemy import String
    from sqlalchemy.orm import aliased

    base_case = (
        select(
            Directory.id,
            Directory.slug,
            Directory.parent_id,
            Directory.slug.cast(String).label("full_path"),
        )
        .where(Directory.parent_id.is_(None))
        .cte(name="dir_path_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        dir_alias.id,
        dir_alias.slug,
        dir_alias.parent_id,
        (base_alias.c.full_path + "/" + dir_alias.slug).label("full_path"),
    ).join(base_alias, dir_alias.parent_id == base_alias.c.id)

    cte = base_case.union_all(recursive_case)
    stmt = select(cte.c.id, cte.c.full_path).where(cte.c.id.in_(directory_ids))
    result = await db.execute(stmt)

    return {row.id: row.full_path for row in result.all()}


async def get_ancestor_map(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    """Return (name_path, slug_path) for each directory_id in a single recursive CTE.

    name_path: space-joined names from root to the directory (inclusive).
    slug_path: slash-joined slugs from root to the directory (inclusive).

    Used by batch indexers to avoid O(depth × n) individual queries.
    """
    if not directory_ids:
        return {}

    from sqlalchemy import String
    from sqlalchemy.orm import aliased

    base_case = (
        select(
            Directory.id,
            Directory.parent_id,
            Directory.name.cast(String).label("name_path"),
            Directory.slug.cast(String).label("slug_path"),
        )
        .where(Directory.parent_id.is_(None))
        .cte(name="ancestor_map_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        dir_alias.id,
        dir_alias.parent_id,
        (base_alias.c.name_path + " " + dir_alias.name).label("name_path"),
        (base_alias.c.slug_path + "/" + dir_alias.slug).label("slug_path"),
    ).join(base_alias, dir_alias.parent_id == base_alias.c.id)

    cte = base_case.union_all(recursive_case)
    stmt = select(cte.c.id, cte.c.name_path, cte.c.slug_path).where(
        cte.c.id.in_(list(directory_ids))
    )
    result = await db.execute(stmt)
    return {row.id: (row.name_path, row.slug_path) for row in result.all()}


async def get_root_directories(
    db: AsyncSession, current_user_id: uuid.UUID | None = None
) -> dict[str, list[dict[str, typing.Any]]]:
    stmt = (
        select(Directory)
        .options(selectinload(Directory.tags))
        .where(Directory.parent_id.is_(None), Directory.is_system.is_(False))
        .order_by(Directory.sort_order, Directory.name)
    )
    result = await db.execute(stmt)
    directories = result.scalars().all()

    items = []
    for d in directories:
        dir_count = await db.scalar(
            select(func.count())
            .select_from(Directory)
            .where(Directory.parent_id == d.id, Directory.is_system.is_(False))
        )
        mat_count = await db.scalar(
            select(func.count())
            .select_from(Material)
            .where(Material.directory_id == d.id, Material.parent_material_id.is_(None))
        )

        is_liked = False
        is_favourited = False
        if current_user_id:
            is_liked = (
                await db.scalar(
                    select(func.count())
                    .select_from(DirectoryLike)
                    .where(
                        DirectoryLike.directory_id == d.id, DirectoryLike.user_id == current_user_id
                    )
                )
                or 0
            ) > 0
            is_favourited = (
                await db.scalar(
                    select(func.count())
                    .select_from(DirectoryFavourite)
                    .where(
                        DirectoryFavourite.directory_id == d.id,
                        DirectoryFavourite.user_id == current_user_id,
                    )
                )
                or 0
            ) > 0

        item = {
            "id": str(d.id),
            "parent_id": str(d.parent_id) if d.parent_id else None,
            "name": d.name,
            "slug": d.slug,
            "type": d.type.value if hasattr(d.type, "value") else d.type,
            "description": d.description,
            "metadata": d.metadata_,
            "sort_order": d.sort_order,
            "is_system": d.is_system,
            "tags": [t.name for t in d.tags],
            "full_path": d.slug,
            "like_count": d.like_count,
            "is_liked": is_liked,
            "is_favourited": is_favourited,
            "created_at": d.created_at,
            "child_directory_count": dir_count or 0,
            "child_material_count": mat_count or 0,
        }
        items.append(item)

    # Also fetch root-level materials (where directory_id is NULL)
    child_material = Material.__table__.alias("child_mat")

    mat_stmt = (
        select(Material)
        .options(
            selectinload(Material.tags),
            selectinload(Material.likes),
            selectinload(Material.favourites),
        )
        .where(Material.directory_id.is_(None), Material.parent_material_id.is_(None))
        .order_by(Material.title)
    )
    mat_result = await db.execute(mat_stmt)

    from app.services.material import material_orm_to_dict, version_orm_to_dict

    materials_out = []
    for material in mat_result.scalars().all():
        # Get attachment count for this material
        att_count = await db.scalar(
            select(func.count())
            .select_from(child_material)
            .where(child_material.c.parent_material_id == material.id)
        )

        # Get latest version
        ver_stmt = select(MaterialVersion).where(
            MaterialVersion.material_id == material.id,
            MaterialVersion.version_number == material.current_version,
        )
        ver_res = await db.execute(ver_stmt)
        version = ver_res.scalar_one_or_none()

        mat_dict = material_orm_to_dict(
            material,
            attachment_count=att_count or 0,
            current_user_id=current_user_id,
            directory_path="",
        )

        if version:
            mat_dict["current_version_info"] = version_orm_to_dict(version)

        materials_out.append(mat_dict)

    return {"directories": items, "materials": materials_out}


async def get_directory_by_id(db: AsyncSession, directory_id: str | uuid.UUID) -> Directory:

    if isinstance(directory_id, str):
        import uuid

        directory_id = uuid.UUID(directory_id)
    result = await db.execute(
        select(Directory).options(selectinload(Directory.tags)).where(Directory.id == directory_id)
    )
    directory = result.scalar_one_or_none()
    if not directory:
        raise NotFoundError("Directory not found")
    return directory


async def get_directory_children(
    db: AsyncSession, directory_id: str | uuid.UUID, current_user_id: uuid.UUID | None = None
) -> dict[str, typing.Any]:

    if isinstance(directory_id, str):
        import uuid

        directory_id = uuid.UUID(directory_id)
    directory = await get_directory_by_id(db, directory_id)

    # Compute full path for children
    path_segments = await get_directory_path(db, directory.id)
    parent_full_path = "/".join([s["slug"] for s in path_segments])

    dir_stmt = (
        select(Directory)
        .options(selectinload(Directory.tags))
        .where(Directory.parent_id == directory.id, Directory.is_system.is_(False))
        .order_by(Directory.sort_order, Directory.name)
    )
    dir_result = await db.execute(dir_stmt)
    child_dirs = dir_result.scalars().all()

    dirs_with_counts = []
    for d in child_dirs:
        dir_count = await db.scalar(
            select(func.count())
            .select_from(Directory)
            .where(Directory.parent_id == d.id, Directory.is_system.is_(False))
        )
        mat_count = await db.scalar(
            select(func.count())
            .select_from(Material)
            .where(Material.directory_id == d.id, Material.parent_material_id.is_(None))
        )

        is_liked = False
        is_favourited = False
        if current_user_id:
            is_liked = (
                await db.scalar(
                    select(func.count())
                    .select_from(DirectoryLike)
                    .where(
                        DirectoryLike.directory_id == d.id, DirectoryLike.user_id == current_user_id
                    )
                )
                or 0
            ) > 0
            is_favourited = (
                await db.scalar(
                    select(func.count())
                    .select_from(DirectoryFavourite)
                    .where(
                        DirectoryFavourite.directory_id == d.id,
                        DirectoryFavourite.user_id == current_user_id,
                    )
                )
                or 0
            ) > 0

        dirs_with_counts.append(
            {
                "id": str(d.id),
                "parent_id": str(d.parent_id) if d.parent_id else None,
                "name": d.name,
                "slug": d.slug,
                "type": d.type.value if hasattr(d.type, "value") else d.type,
                "description": d.description,
                "metadata": d.metadata_,
                "sort_order": d.sort_order,
                "is_system": d.is_system,
                "tags": [t.name for t in d.tags],
                "full_path": f"{parent_full_path}/{d.slug}" if parent_full_path else d.slug,
                "like_count": d.like_count,
                "is_liked": is_liked,
                "is_favourited": is_favourited,
                "created_at": d.created_at,
                "child_directory_count": dir_count or 0,
                "child_material_count": mat_count or 0,
            }
        )

    # Fetch materials and their corresponding latest versions
    mat_stmt = (
        select(Material)
        .options(
            selectinload(Material.tags),
            selectinload(Material.likes),
            selectinload(Material.favourites),
        )
        .where(Material.directory_id == directory.id, Material.parent_material_id.is_(None))
        .order_by(Material.title)
    )
    mat_result = await db.execute(mat_stmt)

    from app.services.material import material_orm_to_dict, version_orm_to_dict

    materials_out = []
    child_material = Material.__table__.alias("child_mat")

    for material in mat_result.scalars().all():
        att_count = await db.scalar(
            select(func.count())
            .select_from(child_material)
            .where(child_material.c.parent_material_id == material.id)
        )

        ver_stmt = select(MaterialVersion).where(
            MaterialVersion.material_id == material.id,
            MaterialVersion.version_number == material.current_version,
        )
        ver_res = await db.execute(ver_stmt)
        version = ver_res.scalar_one_or_none()

        mat_dict = material_orm_to_dict(
            material,
            attachment_count=att_count or 0,
            current_user_id=current_user_id,
            directory_path=parent_full_path,
        )

        if version:
            mat_dict["current_version_info"] = version_orm_to_dict(version)
        materials_out.append(mat_dict)

    return {"directories": dirs_with_counts, "materials": materials_out}


async def get_directory_path(
    db: AsyncSession, directory_id: str | uuid.UUID
) -> list[dict[str, typing.Any]]:

    if isinstance(directory_id, str):
        import uuid

        directory_id = uuid.UUID(directory_id)
    path: list[dict[str, typing.Any]] = []
    current: Directory | None = await get_directory_by_id(db, directory_id)
    seen: set[uuid.UUID] = set()

    while current:
        if current.id in seen:
            break  # circular parent_id — stop traversal
        seen.add(current.id)
        path.insert(0, {"id": str(current.id), "name": current.name, "slug": current.slug})
        if current.parent_id:
            result = await db.execute(select(Directory).where(Directory.id == current.parent_id))
            current = result.scalar_one_or_none()
        else:
            break

    return path


async def resolve_browse_path(
    db: AsyncSession, path: str, current_user_id: uuid.UUID | None = None
) -> dict[str, typing.Any]:
    segments = [s for s in path.split("/") if s]

    if not segments:
        roots = await get_root_directories(db, current_user_id=current_user_id)
        return {"type": "directory_listing", "directories": roots, "materials": []}

    current_dir: Directory | None = None
    last_material: Material | None = None

    from app.services.material import material_orm_to_dict, version_orm_to_dict

    for i, segment in enumerate(segments):
        if segment == "attachments" and last_material is not None:
            # If there are more segments after 'attachments', resolve a specific attachment
            remaining = segments[i + 1 :]
            if remaining:
                att_slug = remaining[0]
                att_result = await db.execute(
                    select(Material)
                    .options(
                        selectinload(Material.likes),
                        selectinload(Material.favourites),
                    )
                    .where(
                        Material.slug == att_slug,
                        Material.parent_material_id == last_material.id,
                    )
                )
                attachment = att_result.scalar_one_or_none()
                if not attachment:
                    raise NotFoundError(f"Attachment '{att_slug}' not found")
                from app.services.material import get_material_with_version

                detail = await get_material_with_version(
                    db, str(attachment.id), current_user_id=current_user_id
                )
                return {"type": "material", "material": detail}

            # No more segments — return the attachment listing
            att_listing_result = await db.execute(
                select(Material)
                .options(
                    selectinload(Material.tags),
                    selectinload(Material.likes),
                    selectinload(Material.favourites),
                )
                .where(Material.parent_material_id == last_material.id)
                .order_by(Material.title)
            )

            materials_out = []
            for material in att_listing_result.scalars().all():
                ver_stmt = select(MaterialVersion).where(
                    MaterialVersion.material_id == material.id,
                    MaterialVersion.version_number == material.current_version,
                )
                ver_res = await db.execute(ver_stmt)
                version = ver_res.scalar_one_or_none()

                mat_dict = material_orm_to_dict(material, current_user_id=current_user_id)
                if version:
                    mat_dict["current_version_info"] = version_orm_to_dict(version)
                materials_out.append(mat_dict)

            return {
                "type": "attachment_listing",
                "materials": materials_out,
                "parent_material": material_orm_to_dict(
                    last_material, current_user_id=current_user_id
                ),
            }

        if current_dir is None:
            dir_result = await db.execute(
                select(Directory)
                .options(selectinload(Directory.tags))
                .where(
                    Directory.slug == segment,
                    Directory.parent_id.is_(None),
                    Directory.is_system.is_(False),
                )
            )
        else:
            dir_result = await db.execute(
                select(Directory)
                .options(selectinload(Directory.tags))
                .where(
                    Directory.slug == segment,
                    Directory.parent_id == current_dir.id,
                    Directory.is_system.is_(False),
                )
            )

        directory: Directory | None = dir_result.scalar_one_or_none()
        if directory:
            current_dir = directory
            last_material = None
            continue

        # If no directory found, check for material in current_dir (or root if current_dir is None)
        mat_result = await db.execute(
            select(Material)
            .options(
                selectinload(Material.tags),
                selectinload(Material.likes),
                selectinload(Material.favourites),
            )
            .where(
                Material.slug == segment,
                Material.directory_id == (current_dir.id if current_dir else None),
                Material.parent_material_id.is_(None),
            )
        )
        mat_row = mat_result.scalar_one_or_none()
        if mat_row:
            last_material = mat_row
            if i == len(segments) - 1:
                from app.services.material import get_material_with_version

                detail = await get_material_with_version(
                    db, str(mat_row.id), current_user_id=current_user_id
                )
                return {"type": "material", "material": detail}
            continue

        raise NotFoundError(f"Path segment '{segment}' not found")

    if current_dir:
        # Populate full_path for the current directory
        path_segments = await get_directory_path(db, current_dir.id)
        current_dir_full_path = "/".join([s["slug"] for s in path_segments])

        is_liked = False
        is_favourited = False
        if current_user_id:
            is_liked = (
                await db.scalar(
                    select(func.count())
                    .select_from(DirectoryLike)
                    .where(
                        DirectoryLike.directory_id == current_dir.id,
                        DirectoryLike.user_id == current_user_id,
                    )
                )
                or 0
            ) > 0
            is_favourited = (
                await db.scalar(
                    select(func.count())
                    .select_from(DirectoryFavourite)
                    .where(
                        DirectoryFavourite.directory_id == current_dir.id,
                        DirectoryFavourite.user_id == current_user_id,
                    )
                )
                or 0
            ) > 0

        children = await get_directory_children(
            db, str(current_dir.id), current_user_id=current_user_id
        )
        return {
            "type": "directory_listing",
            "directory": {
                "id": str(current_dir.id),
                "parent_id": str(current_dir.parent_id) if current_dir.parent_id else None,
                "name": current_dir.name,
                "slug": current_dir.slug,
                "type": current_dir.type.value
                if hasattr(current_dir.type, "value")
                else current_dir.type,
                "description": current_dir.description,
                "metadata": current_dir.metadata_,
                "sort_order": current_dir.sort_order,
                "is_system": current_dir.is_system,
                "full_path": current_dir_full_path,
                "tags": [t.name for t in current_dir.tags],
                "like_count": current_dir.like_count,
                "is_liked": is_liked,
                "is_favourited": is_favourited,
                "created_at": current_dir.created_at,
            },
            "directories": children["directories"],
            "materials": children["materials"],
        }

    raise NotFoundError("Path not found")


async def toggle_directory_like(
    db: AsyncSession, user_id: uuid.UUID, directory_id: uuid.UUID
) -> bool:
    """Toggle a like for a directory. Returns True if liked, False if unliked."""
    result = await db.execute(
        select(DirectoryLike).where(
            DirectoryLike.user_id == user_id, DirectoryLike.directory_id == directory_id
        )
    )
    like = result.scalar_one_or_none()

    if like:
        await db.delete(like)
        await db.execute(
            update(Directory)
            .where(Directory.id == directory_id)
            .values(like_count=Directory.like_count - 1)
        )
        liked = False
    else:
        new_like = DirectoryLike(id=uuid.uuid4(), user_id=user_id, directory_id=directory_id)
        db.add(new_like)
        await db.execute(
            update(Directory)
            .where(Directory.id == directory_id)
            .values(like_count=Directory.like_count + 1)
        )
        liked = True

    await db.flush()
    return liked


async def toggle_directory_favourite(
    db: AsyncSession, user_id: uuid.UUID, directory_id: uuid.UUID
) -> bool:
    """Toggle a favourite for a directory. Returns True if favourited, False if removed."""
    result = await db.execute(
        select(DirectoryFavourite).where(
            DirectoryFavourite.user_id == user_id, DirectoryFavourite.directory_id == directory_id
        )
    )
    favourite = result.scalar_one_or_none()

    if favourite:
        await db.delete(favourite)
        favourited = False
    else:
        new_favourite = DirectoryFavourite(
            id=uuid.uuid4(), user_id=user_id, directory_id=directory_id
        )
        db.add(new_favourite)
        favourited = True

    await db.flush()
    return favourited
