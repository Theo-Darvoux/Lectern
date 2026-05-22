import re
import typing
import unicodedata
import uuid

from sqlalchemy import func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

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

    dir_ids = [d.id for d in directories]

    # Batch: child directory counts per parent
    dir_count_rows = await db.execute(
        select(Directory.parent_id, func.count().label("cnt"))
        .where(Directory.parent_id.in_(dir_ids), Directory.is_system.is_(False))
        .group_by(Directory.parent_id)
    )
    dir_counts: dict[uuid.UUID, int] = {r.parent_id: r.cnt for r in dir_count_rows.all()}

    # Batch: child material counts per directory
    mat_count_rows = await db.execute(
        select(Material.directory_id, func.count().label("cnt"))
        .where(Material.directory_id.in_(dir_ids), Material.parent_material_id.is_(None))
        .group_by(Material.directory_id)
    )
    mat_counts: dict[uuid.UUID, int] = {r.directory_id: r.cnt for r in mat_count_rows.all()}

    # Batch: liked / favourited sets for the current user
    liked_ids: set[uuid.UUID] = set()
    favourited_ids: set[uuid.UUID] = set()
    if current_user_id and dir_ids:
        like_rows = await db.execute(
            select(DirectoryLike.directory_id)
            .where(DirectoryLike.user_id == current_user_id, DirectoryLike.directory_id.in_(dir_ids))
        )
        liked_ids = {r.directory_id for r in like_rows.all()}

        fav_rows = await db.execute(
            select(DirectoryFavourite.directory_id)
            .where(DirectoryFavourite.user_id == current_user_id, DirectoryFavourite.directory_id.in_(dir_ids))
        )
        favourited_ids = {r.directory_id for r in fav_rows.all()}

    items = []
    for d in directories:
        items.append({
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
            "is_liked": d.id in liked_ids,
            "is_favourited": d.id in favourited_ids,
            "created_at": d.created_at,
            "child_directory_count": dir_counts.get(d.id, 0),
            "child_material_count": mat_counts.get(d.id, 0),
        })

    # Root-level materials
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
    root_materials = mat_result.scalars().all()

    from app.services.material import material_orm_to_dict, version_orm_to_dict

    materials_out = await _attach_version_and_counts(db, root_materials, current_user_id, "")
    return {"directories": items, "materials": materials_out}


async def _attach_version_and_counts(
    db: AsyncSession,
    materials: list[Material],
    current_user_id: uuid.UUID | None,
    directory_path: str,
) -> list[dict[str, typing.Any]]:
    """Batch-fetch attachment counts and current versions for a list of materials."""
    from app.services.material import material_orm_to_dict, version_orm_to_dict

    if not materials:
        return []

    mat_ids = [m.id for m in materials]

    # Batch attachment counts
    att_rows = await db.execute(
        select(Material.parent_material_id, func.count().label("cnt"))
        .where(Material.parent_material_id.in_(mat_ids))
        .group_by(Material.parent_material_id)
    )
    att_counts: dict[uuid.UUID, int] = {r.parent_material_id: r.cnt for r in att_rows.all()}

    # Batch version fetch — one query for all materials
    ver_rows = await db.execute(
        select(MaterialVersion).where(MaterialVersion.material_id.in_(mat_ids))
    )
    all_versions: dict[tuple[uuid.UUID, int], MaterialVersion] = {}
    for v in ver_rows.scalars().all():
        all_versions[(v.material_id, v.version_number)] = v

    out = []
    for material in materials:
        version = all_versions.get((material.id, material.current_version))
        mat_dict = material_orm_to_dict(
            material,
            attachment_count=att_counts.get(material.id, 0),
            current_user_id=current_user_id,
            directory_path=directory_path,
        )
        if version:
            mat_dict["current_version_info"] = version_orm_to_dict(version)
        out.append(mat_dict)
    return out


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
        directory_id = uuid.UUID(directory_id)
    directory = await get_directory_by_id(db, directory_id)

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

    child_dir_ids = [d.id for d in child_dirs]

    # Batch: grandchild directory counts
    gc_dir_rows = await db.execute(
        select(Directory.parent_id, func.count().label("cnt"))
        .where(Directory.parent_id.in_(child_dir_ids), Directory.is_system.is_(False))
        .group_by(Directory.parent_id)
    )
    gc_dir_counts: dict[uuid.UUID, int] = {r.parent_id: r.cnt for r in gc_dir_rows.all()}

    # Batch: grandchild material counts
    gc_mat_rows = await db.execute(
        select(Material.directory_id, func.count().label("cnt"))
        .where(Material.directory_id.in_(child_dir_ids), Material.parent_material_id.is_(None))
        .group_by(Material.directory_id)
    )
    gc_mat_counts: dict[uuid.UUID, int] = {r.directory_id: r.cnt for r in gc_mat_rows.all()}

    # Batch: liked / favourited child directories
    liked_dir_ids: set[uuid.UUID] = set()
    favourited_dir_ids: set[uuid.UUID] = set()
    if current_user_id and child_dir_ids:
        like_rows = await db.execute(
            select(DirectoryLike.directory_id)
            .where(DirectoryLike.user_id == current_user_id, DirectoryLike.directory_id.in_(child_dir_ids))
        )
        liked_dir_ids = {r.directory_id for r in like_rows.all()}

        fav_rows = await db.execute(
            select(DirectoryFavourite.directory_id)
            .where(DirectoryFavourite.user_id == current_user_id, DirectoryFavourite.directory_id.in_(child_dir_ids))
        )
        favourited_dir_ids = {r.directory_id for r in fav_rows.all()}

    dirs_with_counts = [
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
            "is_liked": d.id in liked_dir_ids,
            "is_favourited": d.id in favourited_dir_ids,
            "created_at": d.created_at,
            "child_directory_count": gc_dir_counts.get(d.id, 0),
            "child_material_count": gc_mat_counts.get(d.id, 0),
        }
        for d in child_dirs
    ]

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
    materials_out = await _attach_version_and_counts(
        db, mat_result.scalars().all(), current_user_id, parent_full_path
    )

    return {"directories": dirs_with_counts, "materials": materials_out}


async def get_directory_path(
    db: AsyncSession, directory_id: str | uuid.UUID
) -> list[dict[str, typing.Any]]:
    """Return [{id, name, slug}] from root to the given directory using a single recursive CTE."""
    if isinstance(directory_id, str):
        directory_id = uuid.UUID(directory_id)

    base_case = (
        select(
            Directory.id,
            Directory.name,
            Directory.slug,
            Directory.parent_id,
            literal(0).label("depth"),
        )
        .where(Directory.id == directory_id)
        .cte(name="dir_path_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        dir_alias.id,
        dir_alias.name,
        dir_alias.slug,
        dir_alias.parent_id,
        (base_alias.c.depth + 1).label("depth"),
    ).join(base_alias, dir_alias.id == base_alias.c.parent_id)

    cte = base_case.union_all(recursive_case)
    result = await db.execute(
        select(cte.c.id, cte.c.name, cte.c.slug).order_by(cte.c.depth.desc())
    )
    return [{"id": str(row.id), "name": row.name, "slug": row.slug} for row in result.all()]


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
