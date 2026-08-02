import re
import typing
import unicodedata
import uuid

from sqlalchemy import String, case, exists, func, literal, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.common.exceptions import NotFoundError
from app.core.common.natural_sorting import natural_sort_key
from app.models.directory import Directory, DirectoryFavourite, DirectoryLike
from app.models.material import Material, MaterialVersion


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")


def directory_orm_to_dict(
    d: Directory,
    *,
    full_path: str,
    is_liked: bool = False,
    is_favourited: bool = False,
    child_directory_count: int | None = None,
    child_material_count: int | None = None,
    preview_material_ids: list[str] | None = None,
) -> dict[str, typing.Any]:
    out: dict[str, typing.Any] = {
        "id": str(d.id),
        "parent_id": str(d.parent_id) if d.parent_id else None,
        "name": d.name,
        "slug": d.slug,
        "type": d.type.value if hasattr(d.type, "value") else d.type,
        "description": d.description,
        "metadata": d.metadata_,
        "sort_order": d.sort_order,
        "tags": [t.name for t in d.tags],
        "full_path": full_path,
        "like_count": d.like_count,
        "is_liked": is_liked,
        "is_favourited": is_favourited,
        "created_at": d.created_at,
        "preview_material_ids": preview_material_ids or [],
    }
    if child_directory_count is not None:
        out["child_directory_count"] = child_directory_count
    if child_material_count is not None:
        out["child_material_count"] = child_material_count
    return out


_PREVIEW_MAX_DEPTH = 32


async def get_preview_material_ids(
    db: AsyncSession, dir_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Return up to four preview material IDs from each bounded subtree.

    Direct materials win over descendants. The recursive depth cap prevents a
    corrupted directory cycle from running forever.
    """
    if not dir_ids:
        return {}

    base = select(
        Directory.id.label("root_id"),
        Directory.id.label("dir_id"),
        literal(0).label("depth"),
    ).where(Directory.id.in_(dir_ids))
    subtree = base.cte("preview_subtree", recursive=True)
    child = aliased(Directory)
    subtree = subtree.union_all(
        select(
            subtree.c.root_id,
            child.id,
            (subtree.c.depth + 1).label("depth"),
        ).where(
            child.parent_id == subtree.c.dir_id,
            child.deleted_at.is_(None),
            subtree.c.depth < _PREVIEW_MAX_DEPTH,
        )
    )

    ranked = (
        select(
            subtree.c.root_id,
            Material.id.label("material_id"),
            func.row_number()
            .over(
                partition_by=subtree.c.root_id,
                order_by=(
                    subtree.c.depth,
                    func.lower(Material.title),
                    Material.id,
                ),
            )
            .label("preview_rank"),
        )
        .join(Material, Material.directory_id == subtree.c.dir_id)
        .where(
            Material.parent_material_id.is_(None),
            Material.deleted_at.is_(None),
        )
        .subquery()
    )

    rows = await db.execute(
        select(ranked.c.root_id, ranked.c.material_id)
        .where(ranked.c.preview_rank <= 4)
        .order_by(ranked.c.root_id, ranked.c.preview_rank)
    )

    result: dict[uuid.UUID, list[str]] = {}
    for root_id, material_id in rows:
        result.setdefault(root_id, []).append(str(material_id))
    return result


async def _update_metadata_key(
    db: AsyncSession, directory_id: uuid.UUID, key: str, value: str | None
) -> None:
    directory = await get_directory_by_id(db, directory_id)
    metadata = dict(directory.metadata_ or {})
    if value is None:
        metadata.pop(key, None)
    else:
        metadata[key] = value
    await db.execute(
        update(Directory).where(Directory.id == directory_id).values(metadata_=metadata)
    )
    await db.commit()


async def update_directory_icon(
    db: AsyncSession, directory_id: uuid.UUID, icon: str | None
) -> None:
    await _update_metadata_key(db, directory_id, "thumbnail_icon", icon)


async def update_directory_color(
    db: AsyncSession, directory_id: uuid.UUID, color: str | None
) -> None:
    await _update_metadata_key(db, directory_id, "thumbnail_color", color)


async def get_directory_paths(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not directory_ids:
        return {}

    # Bottom-up recursive CTE starting ONLY from the requested IDs.
    base_case = (
        select(
            Directory.id.label("start_id"),
            Directory.id,
            Directory.slug,
            Directory.parent_id,
            literal(0).label("depth"),
        )
        .where(Directory.id.in_(directory_ids))
        .cte(name="dir_path_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        base_alias.c.start_id,
        dir_alias.id,
        dir_alias.slug,
        dir_alias.parent_id,
        (base_alias.c.depth + 1).label("depth"),
    ).join(base_alias, dir_alias.id == base_alias.c.parent_id)

    cte = base_case.union_all(recursive_case)

    # Order by depth descending so that when we iterate, we see the root-most slug first.
    stmt = select(cte.c.start_id, cte.c.slug).order_by(cte.c.start_id, cte.c.depth.desc())
    result = await db.execute(stmt)

    paths: dict[uuid.UUID, list[str]] = {}
    for start_id, slug in result.all():
        paths.setdefault(start_id, []).append(slug)

    return {k: "/".join(v) for k, v in paths.items()}


async def get_ancestor_map(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    """Return (name_path, slug_path) for each directory_id using a bottom-up recursive CTE.

    name_path: space-joined names from root to the directory (inclusive).
    slug_path: slash-joined slugs from root to the directory (inclusive).

    Used by batch indexers to avoid O(depth × n) individual queries.
    """
    if not directory_ids:
        return {}

    base_case = (
        select(
            Directory.id.label("start_id"),
            Directory.id,
            Directory.parent_id,
            Directory.name,
            Directory.slug,
            literal(0).label("depth"),
        )
        .where(Directory.id.in_(directory_ids))
        .cte(name="ancestor_map_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        base_alias.c.start_id,
        dir_alias.id,
        dir_alias.parent_id,
        dir_alias.name,
        dir_alias.slug,
        (base_alias.c.depth + 1).label("depth"),
    ).join(base_alias, dir_alias.id == base_alias.c.parent_id)

    cte = base_case.union_all(recursive_case)
    stmt = select(cte.c.start_id, cte.c.name, cte.c.slug).order_by(
        cte.c.start_id, cte.c.depth.desc()
    )
    result = await db.execute(stmt)

    paths: dict[uuid.UUID, tuple[list[str], list[str]]] = {}
    for start_id, name, slug in result.all():
        if start_id not in paths:
            paths[start_id] = ([], [])
        paths[start_id][0].append(name)
        paths[start_id][1].append(slug)

    return {k: (" ".join(v[0]), "/".join(v[1])) for k, v in paths.items()}


async def get_root_directories(
    db: AsyncSession, current_user_id: uuid.UUID | None = None
) -> dict[str, list[dict[str, typing.Any]]]:
    stmt = (
        select(Directory).options(selectinload(Directory.tags)).where(Directory.parent_id.is_(None))
    )
    result = await db.execute(stmt)
    # sort_order stays primary; natural order on name breaks ties ("Chapitre 2"
    # before "Chapitre 10"). Natural sort has no portable SQL form, so order here.
    directories = sorted(
        result.scalars().all(),
        key=lambda d: (d.sort_order, natural_sort_key(d.name)),
    )

    dir_ids = [d.id for d in directories]

    # Batch: child directory counts per parent
    dir_count_rows = await db.execute(
        select(Directory.parent_id, func.count().label("cnt"))
        .where(Directory.parent_id.in_(dir_ids))
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
            select(DirectoryLike.directory_id).where(
                DirectoryLike.user_id == current_user_id, DirectoryLike.directory_id.in_(dir_ids)
            )
        )
        liked_ids = {r.directory_id for r in like_rows.all()}

        fav_rows = await db.execute(
            select(DirectoryFavourite.directory_id).where(
                DirectoryFavourite.user_id == current_user_id,
                DirectoryFavourite.directory_id.in_(dir_ids),
            )
        )
        favourited_ids = {r.directory_id for r in fav_rows.all()}

    preview_ids = await get_preview_material_ids(db, dir_ids)

    items = [
        directory_orm_to_dict(
            d,
            full_path=d.slug,
            is_liked=d.id in liked_ids,
            is_favourited=d.id in favourited_ids,
            child_directory_count=dir_counts.get(d.id, 0),
            child_material_count=mat_counts.get(d.id, 0),
            preview_material_ids=preview_ids.get(d.id, []),
        )
        for d in directories
    ]

    # Root-level materials. is_liked/is_favourited are resolved in a batched
    # query inside _attach_version_and_counts, so we don't eagerly load the
    # full likes/favourites collections here.
    mat_stmt = (
        select(Material)
        .options(selectinload(Material.tags))
        .where(Material.directory_id.is_(None), Material.parent_material_id.is_(None))
    )
    mat_result = await db.execute(mat_stmt)
    root_materials = sorted(mat_result.scalars().all(), key=lambda m: natural_sort_key(m.title))

    materials_out = await _attach_version_and_counts(db, root_materials, current_user_id, "")
    return {"directories": items, "materials": materials_out}


async def _attach_version_and_counts(
    db: AsyncSession,
    materials: list[Material] | typing.Sequence[Material],
    current_user_id: uuid.UUID | None,
    directory_path: str | None,
) -> list[dict[str, typing.Any]]:
    """Batch-fetch attachment counts and current versions for a list of materials."""
    from app.services.material import get_liked_favourited_sets, material_orm_to_dict

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

    # Batch version fetch — one query for all materials, restricted to the
    # current version of each material so historical rows are never loaded.
    current_version_pairs = [(m.id, m.current_version) for m in materials]
    ver_rows = await db.execute(
        select(MaterialVersion).where(
            tuple_(MaterialVersion.material_id, MaterialVersion.version_number).in_(
                current_version_pairs
            )
        )
    )
    all_versions: dict[tuple[uuid.UUID, int], MaterialVersion] = {}
    for v in ver_rows.scalars().all():
        all_versions[(v.material_id, v.version_number)] = v

    # Batch: liked / favourited sets for the current user. Avoids loading the
    # full likes/favourites collections per material (like_count is a column).
    liked_ids, favourited_ids = await get_liked_favourited_sets(db, current_user_id, mat_ids)

    return [
        material_orm_to_dict(
            material,
            attachment_count=att_counts.get(material.id, 0),
            current_user_id=current_user_id,
            directory_path=directory_path,
            is_liked=material.id in liked_ids,
            is_favourited=material.id in favourited_ids,
            current_version=all_versions.get((material.id, material.current_version)),
        )
        for material in materials
    ]


async def get_directory_by_id(db: AsyncSession, directory_id: str | uuid.UUID) -> Directory:
    if isinstance(directory_id, str):
        directory_id = uuid.UUID(directory_id)
    result = await db.execute(
        select(Directory).options(selectinload(Directory.tags)).where(Directory.id == directory_id)
    )
    directory = result.scalar_one_or_none()
    if not directory:
        raise NotFoundError("Directory not found")
    return directory


async def get_directory_children(
    db: AsyncSession,
    directory_id: str | uuid.UUID,
    current_user_id: uuid.UUID | None = None,
    *,
    directory: Directory | None = None,
    parent_full_path: str | None = None,
) -> dict[str, typing.Any]:
    """List a directory's child directories and materials.

    ``directory`` and ``parent_full_path`` may be supplied by callers that have
    already loaded the directory / resolved its full path (e.g.
    ``resolve_browse_path``) to avoid an extra PK lookup and a redundant
    recursive path CTE.
    """
    if isinstance(directory_id, str):
        directory_id = uuid.UUID(directory_id)
    if directory is None or directory.id != directory_id:
        directory = await get_directory_by_id(db, directory_id)

    if parent_full_path is None:
        path_segments = await get_directory_path(db, directory.id)
        parent_full_path = "/".join([s["slug"] for s in path_segments])

    dir_stmt = (
        select(Directory)
        .options(selectinload(Directory.tags))
        .where(Directory.parent_id == directory.id)
    )
    dir_result = await db.execute(dir_stmt)
    child_dirs = sorted(
        dir_result.scalars().all(),
        key=lambda d: (d.sort_order, natural_sort_key(d.name)),
    )

    child_dir_ids = [d.id for d in child_dirs]

    # Batch: grandchild directory counts
    gc_dir_rows = await db.execute(
        select(Directory.parent_id, func.count().label("cnt"))
        .where(Directory.parent_id.in_(child_dir_ids))
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
            select(DirectoryLike.directory_id).where(
                DirectoryLike.user_id == current_user_id,
                DirectoryLike.directory_id.in_(child_dir_ids),
            )
        )
        liked_dir_ids = {r.directory_id for r in like_rows.all()}

        fav_rows = await db.execute(
            select(DirectoryFavourite.directory_id).where(
                DirectoryFavourite.user_id == current_user_id,
                DirectoryFavourite.directory_id.in_(child_dir_ids),
            )
        )
        favourited_dir_ids = {r.directory_id for r in fav_rows.all()}

    preview_ids = await get_preview_material_ids(db, child_dir_ids)

    dirs_with_counts = [
        directory_orm_to_dict(
            d,
            full_path=f"{parent_full_path}/{d.slug}" if parent_full_path else d.slug,
            is_liked=d.id in liked_dir_ids,
            is_favourited=d.id in favourited_dir_ids,
            child_directory_count=gc_dir_counts.get(d.id, 0),
            child_material_count=gc_mat_counts.get(d.id, 0),
            preview_material_ids=preview_ids.get(d.id, []),
        )
        for d in child_dirs
    ]

    mat_stmt = (
        select(Material)
        .options(selectinload(Material.tags))
        .where(Material.directory_id == directory.id, Material.parent_material_id.is_(None))
    )
    mat_result = await db.execute(mat_stmt)
    sorted_mats = sorted(mat_result.scalars().all(), key=lambda m: natural_sort_key(m.title))
    materials_out = await _attach_version_and_counts(
        db, sorted_mats, current_user_id, parent_full_path
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
    result = await db.execute(select(cte.c.id, cte.c.name, cte.c.slug).order_by(cte.c.depth.desc()))
    return [{"id": str(row.id), "name": row.name, "slug": row.slug} for row in result.all()]


async def resolve_browse_path(
    db: AsyncSession, path: str, current_user_id: uuid.UUID | None = None
) -> dict[str, typing.Any]:
    segments = [s for s in path.split("/") if s]

    if not segments:
        roots = await get_root_directories(db, current_user_id=current_user_id)
        return {"type": "directory_listing", **roots}

    current_dir: Directory | None = None
    last_material: Material | None = None

    # Resolve the directory chain in a single query rather than one round-trip
    # per segment: fetch every directory whose slug appears in the path, then
    # walk the chain in-memory keyed by (parent_id, slug). Unrelated directories
    # that happen to share a slug are simply never matched during the walk.
    unique_slugs = list(set(segments))
    dir_result = await db.execute(
        select(Directory)
        .options(selectinload(Directory.tags))
        .where(Directory.slug.in_(unique_slugs))
    )
    dir_map = {(d.parent_id, d.slug): d for d in dir_result.scalars().all()}

    # Also fetch candidate materials for the very last segment
    last_segment = segments[-1]
    mat_result = await db.execute(
        select(Material)
        .options(selectinload(Material.tags))
        .where(Material.slug == last_segment, Material.parent_material_id.is_(None))
    )
    mat_map = {m.directory_id: m for m in mat_result.scalars().all()}

    for i, segment in enumerate(segments):
        parent_key = current_dir.id if current_dir else None

        # Check for directory
        nxt_dir = dir_map.get((parent_key, segment))
        if nxt_dir:
            current_dir = nxt_dir
            continue

        # If no directory found, check for material in current_dir (or root if current_dir is None)
        if i == len(segments) - 1:
            mat = mat_map.get(parent_key)
            if mat:
                from app.services.material import get_material_with_version

                detail = await get_material_with_version(
                    db, str(mat.id), current_user_id=current_user_id
                )
                return {"type": "material", "material": detail}

        raise NotFoundError(f"Path segment '{segment}' not found")

    if current_dir:
        # Resolve the directory's path once and reuse it for the directory's own
        # full_path, the children listing, and the breadcrumbs (avoids running
        # the recursive path CTE three times per navigation).
        path_segments = await get_directory_path(db, current_dir.id)
        current_dir_full_path = "/".join([s["slug"] for s in path_segments])

        is_liked = False
        is_favourited = False
        if current_user_id:
            is_liked = bool(
                await db.scalar(
                    select(
                        exists().where(
                            DirectoryLike.directory_id == current_dir.id,
                            DirectoryLike.user_id == current_user_id,
                        )
                    )
                )
            )
            is_favourited = bool(
                await db.scalar(
                    select(
                        exists().where(
                            DirectoryFavourite.directory_id == current_dir.id,
                            DirectoryFavourite.user_id == current_user_id,
                        )
                    )
                )
            )

        children = await get_directory_children(
            db,
            str(current_dir.id),
            current_user_id=current_user_id,
            directory=current_dir,
            parent_full_path=current_dir_full_path,
        )
        return {
            "type": "directory_listing",
            "directory": directory_orm_to_dict(
                current_dir,
                full_path=current_dir_full_path,
                is_liked=is_liked,
                is_favourited=is_favourited,
            ),
            "directories": children["directories"],
            "materials": children["materials"],
            "_breadcrumbs": path_segments,
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


_DOWNLOAD_MAX_FILES = 500
_DOWNLOAD_MAX_BYTES = 500 * 1024 * 1024  # 500 MiB


async def _build_zip_entries(
    db: AsyncSession,
    cte: typing.Any,
) -> list[tuple[str, str]]:
    """Execute the ZIP content query for a directory tree CTE and build deduplicated arcnames."""
    stmt = (
        select(
            cte.c.rel_path,
            MaterialVersion.file_key,
            MaterialVersion.file_name,
            MaterialVersion.file_size,
        )
        .join(Material, Material.directory_id == cte.c.id)
        .join(
            MaterialVersion,
            (MaterialVersion.material_id == Material.id)
            & (MaterialVersion.version_number == Material.current_version),
        )
        .where(
            Material.parent_material_id.is_(None),
            MaterialVersion.file_key.isnot(None),
            ~MaterialVersion.file_key.like("quarantine/%"),
        )
    )

    rows = sorted(
        (await db.execute(stmt)).all(),
        key=lambda r: (natural_sort_key(r.rel_path), natural_sort_key(r.file_name)),
    )

    # Fetch attachments (child materials) and place them under a subfolder
    # named after the parent material's file stem.
    parent_version = aliased(MaterialVersion)
    attachment_material = aliased(Material)
    attachment_version = aliased(MaterialVersion)

    attach_stmt = (
        select(
            cte.c.rel_path,
            parent_version.file_name.label("parent_file_name"),
            attachment_version.file_key,
            attachment_version.file_name,
            attachment_version.file_size,
        )
        .join(Material, Material.directory_id == cte.c.id)
        .join(
            parent_version,
            (parent_version.material_id == Material.id)
            & (parent_version.version_number == Material.current_version),
        )
        .join(attachment_material, attachment_material.parent_material_id == Material.id)
        .join(
            attachment_version,
            (attachment_version.material_id == attachment_material.id)
            & (attachment_version.version_number == attachment_material.current_version),
        )
        .where(
            Material.parent_material_id.is_(None),
            Material.deleted_at.is_(None),
            attachment_material.deleted_at.is_(None),
            parent_version.deleted_at.is_(None),
            attachment_version.file_key.isnot(None),
            ~attachment_version.file_key.like("quarantine/%"),
            attachment_version.deleted_at.is_(None),
        )
        # Bypass the global soft-delete event listener: it generates unaliased
        # `material_versions.deleted_at` in ON clauses which SQLite rejects.
        # Explicit conditions above handle filtering instead.
        .execution_options(include_deleted=True)
    )

    attachment_rows = sorted(
        (await db.execute(attach_stmt)).all(),
        key=lambda r: (
            natural_sort_key(r.rel_path),
            natural_sort_key(r.parent_file_name),
            natural_sort_key(r.file_name),
        ),
    )

    total_count = len(rows) + len(attachment_rows)
    if total_count > _DOWNLOAD_MAX_FILES:
        raise ValueError(
            f"This directory contains too many files ({total_count}); limit is {_DOWNLOAD_MAX_FILES}."
        )

    total = sum(r.file_size or 0 for r in rows) + sum(r.file_size or 0 for r in attachment_rows)
    if total > _DOWNLOAD_MAX_BYTES:
        limit_mb = _DOWNLOAD_MAX_BYTES // (1024 * 1024)
        raise ValueError(
            f"This directory is too large to download as a ZIP (limit: {limit_mb} MiB)."
        )

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add_entry(arcname: str, file_key: str) -> None:
        original = arcname
        n = 1
        while arcname in seen:
            base, _, ext = original.rpartition(".")
            arcname = f"{base}_{n}.{ext}" if ext else f"{original}_{n}"
            n += 1
        seen.add(arcname)
        entries.append((arcname, file_key))

    for row in rows:
        fname = row.file_name or "file"
        arcname = f"{row.rel_path}/{fname}" if row.rel_path else fname
        _add_entry(arcname, row.file_key)

    for row in attachment_rows:
        fname = row.file_name or "file"
        parent_name = row.parent_file_name or "attachments"
        parent_stem = parent_name.rsplit(".", 1)[0] if "." in parent_name else parent_name
        folder = f"{row.rel_path}/{parent_stem}" if row.rel_path else parent_stem
        _add_entry(f"{folder}/{fname}", row.file_key)

    return entries


async def get_directory_download_entries(
    db: AsyncSession,
    directory_id: uuid.UUID,
) -> tuple[str, list[tuple[str, str]]]:
    """Return (directory_name, [(arcname, file_key), ...]) for building a ZIP download.

    arcname preserves the subdirectory structure relative to the requested directory.
    Raises ValueError when the directory exceeds safety limits.
    """
    root = await get_directory_by_id(db, directory_id)

    base_case = (
        select(
            Directory.id,
            literal("").cast(String).label("rel_path"),
        )
        .where(Directory.id == directory_id)
        .cte(name="dir_tree", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        dir_alias.id,
        case(
            (base_alias.c.rel_path == "", dir_alias.name),
            else_=base_alias.c.rel_path + "/" + dir_alias.name,
        ).label("rel_path"),
    ).join(base_alias, dir_alias.parent_id == base_alias.c.id)

    cte = base_case.union_all(recursive_case)
    return root.name, await _build_zip_entries(db, cte)


async def get_root_download_entries(
    db: AsyncSession,
) -> tuple[str, list[tuple[str, str]]]:
    """Return ("root", [(arcname, file_key), ...]) for downloading the entire root level."""
    base_case = (
        select(
            Directory.id,
            Directory.name.cast(String).label("rel_path"),
        )
        .where(Directory.parent_id.is_(None))
        .cte(name="dir_tree", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        dir_alias.id,
        (base_alias.c.rel_path + "/" + dir_alias.name).label("rel_path"),
    ).join(base_alias, dir_alias.parent_id == base_alias.c.id)

    cte = base_case.union_all(recursive_case)
    return "root", await _build_zip_entries(db, cte)
