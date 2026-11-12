import asyncio
import json
import logging
import re
import typing
import unicodedata
import uuid

from sqlalchemy import String, case, exists, func, literal, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.common.exceptions import NotFoundError
from app.core.common.natural_sorting import content_status_sort_key, natural_sort_key
from app.core.database.content_generation import get_content_generation
from app.models.directory import Directory, DirectoryFavourite, DirectoryLike
from app.models.material import Material, MaterialVersion
from app.services.reaction_lock import acquire_reaction_toggle_lock

logger = logging.getLogger(__name__)


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
        "status": getattr(d, "status", "current") or "current",
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
_PREVIEW_CACHE_PREFIX = "dir-preview:v1"
# Safety net: entries are orphaned by the content generation on every relevant
# commit, and expire on their own for changes made where Redis was unreachable.
_PREVIEW_CACHE_TTL_SECONDS = 600


def _redis_client() -> typing.Any:
    from app.core.database import redis as redis_module

    return redis_module.redis_client


async def get_preview_material_ids(
    db: AsyncSession, dir_ids: list[uuid.UUID], redis: typing.Any = None
) -> dict[uuid.UUID, list[str]]:
    """Return up to four preview material IDs from each bounded subtree.

    Direct materials win over descendants. Results are cached per directory
    under the browse content generation (see ``content_generation``), because
    the subtree walk is the costliest query of a listing.
    """
    if not dir_ids:
        return {}
    redis = redis if redis is not None else _redis_client()
    generation = await get_content_generation(redis)
    if generation is None:
        return await _compute_preview_material_ids(db, dir_ids)

    keys = [f"{_PREVIEW_CACHE_PREFIX}:{generation}:{dir_id}" for dir_id in dir_ids]
    result: dict[uuid.UUID, list[str]] = {}
    misses: list[uuid.UUID] = []
    try:
        cached = list(await redis.mget(keys))
        if len(cached) != len(dir_ids):
            raise ValueError("unexpected MGET reply length")
    except Exception as exc:
        logger.debug("Preview cache read failed: %s", exc)
        cached = [None] * len(dir_ids)
    for dir_id, raw in zip(dir_ids, cached, strict=True):
        try:
            ids = json.loads(raw) if raw is not None else None
        except (TypeError, ValueError):
            ids = None
        if not isinstance(ids, list):
            misses.append(dir_id)
        elif ids:
            result[dir_id] = [str(i) for i in ids]
    if not misses:
        return result

    computed = await _compute_preview_material_ids(db, misses)
    result.update(computed)
    try:
        pipe = redis.pipeline(transaction=False)
        for dir_id in misses:
            pipe.set(
                f"{_PREVIEW_CACHE_PREFIX}:{generation}:{dir_id}",
                json.dumps(computed.get(dir_id, [])),
                ex=_PREVIEW_CACHE_TTL_SECONDS,
            )
        await pipe.execute()
    except Exception as exc:
        logger.debug("Preview cache write failed: %s", exc)
    return result


async def attach_preview_thumbnails(
    db: AsyncSession, directories: list[dict[str, typing.Any]], redis: typing.Any = None
) -> None:
    """Add ``preview_thumbnails`` (signed WebP URLs) to serialized directories.

    One query covers every preview material of the listing, so collage cards
    need no request of their own. A material without a generated WebP gets a
    null URL (the card shows a file icon). Signing failures leave the field
    unset and the client falls back to ``GET /materials/{id}/thumbnail``.
    """
    from app.services.material import resolve_thumbnail

    wanted = {mid for d in directories for mid in d.get("preview_material_ids") or []}
    if not wanted:
        for d in directories:
            d["preview_thumbnails"] = []
        return
    rows = await db.execute(
        select(
            Material.id,
            MaterialVersion.thumbnail_key,
            MaterialVersion.file_key,
            MaterialVersion.file_mime_type,
            MaterialVersion.file_name,
        )
        .join(MaterialVersion, MaterialVersion.material_id == Material.id)
        .where(
            Material.id.in_([uuid.UUID(mid) for mid in wanted]),
            MaterialVersion.version_number == Material.current_version,
        )
    )
    versions = {
        str(row.id): {
            "thumbnail_key": row.thumbnail_key,
            "file_key": row.file_key,
            "file_mime_type": row.file_mime_type,
            "file_name": row.file_name,
        }
        for row in rows
    }
    redis = redis if redis is not None else _redis_client()

    async def sign(mid: str) -> str | None:
        version = versions.get(mid)
        if version is None:
            return None
        resolved = await resolve_thumbnail(version, redis)
        # Only a generated WebP fits a collage cell; a "fallback" may be a PDF.
        return resolved[0] if resolved and resolved[1] == "webp" else None

    ids = sorted(wanted)
    try:
        urls = dict(zip(ids, await asyncio.gather(*(sign(mid) for mid in ids)), strict=True))
    except Exception as exc:
        logger.warning("Could not sign directory preview thumbnails: %s", exc)
        return
    for d in directories:
        d["preview_thumbnails"] = [
            {"material_id": mid, "url": urls.get(mid)}
            for mid in d.get("preview_material_ids") or []
        ]


async def _child_counts(
    db: AsyncSession, dir_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """Child directory and top-level material counts per directory."""
    if not dir_ids:
        return {}, {}
    dir_rows = await db.execute(
        select(Directory.parent_id, func.count().label("cnt"))
        .where(Directory.parent_id.in_(dir_ids))
        .group_by(Directory.parent_id)
    )
    mat_rows = await db.execute(
        select(Material.directory_id, func.count().label("cnt"))
        .where(Material.directory_id.in_(dir_ids), Material.parent_material_id.is_(None))
        .group_by(Material.directory_id)
    )
    return (
        {r.parent_id: r.cnt for r in dir_rows.all()},
        {r.directory_id: r.cnt for r in mat_rows.all()},
    )


def _sort_directories(directories: typing.Iterable[Directory]) -> list[Directory]:
    # sort_order stays primary; natural order on name breaks ties ("Chapitre 2"
    # before "Chapitre 10"). Natural sort has no portable SQL form, so order here.
    return sorted(
        directories,
        key=lambda d: (
            content_status_sort_key(getattr(d, "status", "current")),
            d.sort_order,
            natural_sort_key(d.name),
        ),
    )


async def _compute_preview_material_ids(
    db: AsyncSession, dir_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """The uncached subtree walk. The depth cap stops a corrupted cycle."""

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
                    case(
                        (Material.status == "important", 0),
                        (Material.status == "current", 1),
                        (Material.status == "deprecated", 2),
                        (Material.status == "archived", 3),
                        else_=1,
                    ),
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
    directories = _sort_directories(result.scalars().all())

    dir_ids = [d.id for d in directories]
    dir_counts, mat_counts = await _child_counts(db, dir_ids)

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
    await attach_preview_thumbnails(db, items)

    # Root-level materials. is_liked/is_favourited are resolved in a batched
    # query inside _attach_version_and_counts, so we don't eagerly load the
    # full likes/favourites collections here.
    mat_stmt = (
        select(Material)
        .options(selectinload(Material.tags))
        .where(Material.directory_id.is_(None), Material.parent_material_id.is_(None))
    )
    mat_result = await db.execute(mat_stmt)
    root_materials = sorted(
        mat_result.scalars().all(),
        key=lambda m: (content_status_sort_key(m.status), natural_sort_key(m.title)),
    )

    materials_out = await _attach_version_and_counts(db, root_materials, current_user_id, "")
    return {"directories": items, "materials": materials_out}


async def _attach_version_and_counts(
    db: AsyncSession,
    materials: list[Material] | typing.Sequence[Material],
    current_user_id: uuid.UUID | None,
    directory_path: str | None,
) -> list[dict[str, typing.Any]]:
    """Batch-fetch attachment counts and current versions for a list of materials."""
    from app.services.material import (
        attach_material_thumbnails,
        get_liked_favourited_sets,
        material_orm_to_dict,
    )

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

    materials_out = [
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
    await attach_material_thumbnails(materials_out)
    return materials_out


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
    child_dirs = _sort_directories(dir_result.scalars().all())

    child_dir_ids = [d.id for d in child_dirs]
    gc_dir_counts, gc_mat_counts = await _child_counts(db, child_dir_ids)

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
    await attach_preview_thumbnails(db, dirs_with_counts)

    mat_stmt = (
        select(Material)
        .options(selectinload(Material.tags))
        .where(Material.directory_id == directory.id, Material.parent_material_id.is_(None))
    )
    mat_result = await db.execute(mat_stmt)
    sorted_mats = sorted(
        mat_result.scalars().all(),
        key=lambda m: (content_status_sort_key(m.status), natural_sort_key(m.title)),
    )
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


async def get_directory_breadcrumb_paths(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, typing.Any]]]:
    """Resolve many root-to-directory breadcrumb paths with one recursive CTE."""
    if not directory_ids:
        return {}

    base_case = (
        select(
            Directory.id,
            Directory.name,
            Directory.slug,
            Directory.parent_id,
            Directory.id.label("requested_id"),
            literal(0).label("depth"),
        )
        .where(Directory.id.in_(directory_ids))
        .cte(name="dir_paths_cte", recursive=True)
    )

    path_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")
    recursive_case = select(
        dir_alias.id,
        dir_alias.name,
        dir_alias.slug,
        dir_alias.parent_id,
        path_alias.c.requested_id,
        (path_alias.c.depth + 1).label("depth"),
    ).join(path_alias, dir_alias.id == path_alias.c.parent_id)

    cte = base_case.union_all(recursive_case)
    rows = (
        await db.execute(
            select(
                cte.c.requested_id,
                cte.c.id,
                cte.c.name,
                cte.c.slug,
                cte.c.depth,
            ).order_by(cte.c.requested_id, cte.c.depth.desc())
        )
    ).all()

    paths: dict[uuid.UUID, list[dict[str, typing.Any]]] = {}
    for row in rows:
        paths.setdefault(row.requested_id, []).append(
            {"id": str(row.id), "name": row.name, "slug": row.slug}
        )
    return paths


async def get_tree_children(
    db: AsyncSession, directory_id: uuid.UUID | None
) -> dict[str, list[dict[str, typing.Any]]]:
    """The light listing behind the directory tree sidebar.

    Only what a tree node shows: names, slugs, paths, child counts and what
    picks a material's icon. No versions, likes, previews or signed URLs.
    ``directory_id`` None lists the root.
    """
    parent_path = ""
    if directory_id is not None:
        await get_directory_by_id(db, directory_id)  # 404 for a missing or deleted folder
        segments = await get_directory_path(db, directory_id)
        parent_path = "/".join(s["slug"] for s in segments)

    dir_parent = (
        Directory.parent_id.is_(None)
        if directory_id is None
        else Directory.parent_id == directory_id
    )
    child_dirs = _sort_directories(
        (await db.execute(select(Directory).where(dir_parent))).scalars()
    )
    dir_counts, mat_counts = await _child_counts(db, [d.id for d in child_dirs])

    mat_parent = (
        Material.directory_id.is_(None)
        if directory_id is None
        else Material.directory_id == directory_id
    )
    mat_rows = (
        await db.execute(
            select(
                Material.id,
                Material.title,
                Material.slug,
                Material.type,
                Material.status,
                Material.metadata_,
                MaterialVersion.file_name,
                MaterialVersion.file_mime_type,
            )
            .outerjoin(
                MaterialVersion,
                (MaterialVersion.material_id == Material.id)
                & (MaterialVersion.version_number == Material.current_version),
            )
            .where(mat_parent, Material.parent_material_id.is_(None))
        )
    ).all()
    mat_rows = sorted(
        mat_rows, key=lambda m: (content_status_sort_key(m.status), natural_sort_key(m.title))
    )

    def child_path(slug: str) -> str:
        return f"{parent_path}/{slug}" if parent_path else slug

    return {
        "directories": [
            {
                "id": str(d.id),
                "parent_id": str(d.parent_id) if d.parent_id else None,
                "name": d.name,
                "slug": d.slug,
                "full_path": child_path(d.slug),
                "child_directory_count": dir_counts.get(d.id, 0),
                "child_material_count": mat_counts.get(d.id, 0),
            }
            for d in child_dirs
        ],
        "materials": [
            {
                "id": str(m.id),
                "title": m.title,
                "slug": m.slug,
                "type": m.type.value if hasattr(m.type, "value") else m.type,
                "file_name": m.file_name,
                "file_mime_type": m.file_mime_type,
                "url": str((m.metadata_ or {}).get("url") or "") or None,
            }
            for m in mat_rows
        ],
    }


def _chain_breadcrumbs(chain: list[Directory]) -> list[dict[str, typing.Any]]:
    return [{"id": str(d.id), "name": d.name, "slug": d.slug} for d in chain]


async def resolve_browse_path(
    db: AsyncSession, path: str, current_user_id: uuid.UUID | None = None
) -> dict[str, typing.Any]:
    segments = [s for s in path.split("/") if s]

    if not segments:
        roots = await get_root_directories(db, current_user_id=current_user_id)
        return {"type": "directory_listing", **roots}

    current_dir: Directory | None = None
    # Directories walked so far: they are the breadcrumbs, so no path CTE is needed.
    chain: list[Directory] = []

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
            chain.append(nxt_dir)
            continue

        # If no directory found, check for material in current_dir (or root if current_dir is None)
        if i == len(segments) - 1:
            mat = mat_map.get(parent_key)
            if mat:
                from app.services.material import get_material_with_version

                detail = await get_material_with_version(
                    db, mat.id, current_user_id=current_user_id, material=mat
                )
                return {
                    "type": "material",
                    "material": detail,
                    "_breadcrumbs": _chain_breadcrumbs(chain),
                }

        raise NotFoundError(f"Path segment '{segment}' not found")

    if current_dir:
        # The walked chain is the directory's path: reuse it for its full_path,
        # the children listing and the breadcrumbs.
        path_segments = _chain_breadcrumbs(chain)
        current_dir_full_path = "/".join(d.slug for d in chain)

        is_liked = False
        is_favourited = False
        if current_user_id:
            flags = (
                await db.execute(
                    select(
                        exists().where(
                            DirectoryLike.directory_id == current_dir.id,
                            DirectoryLike.user_id == current_user_id,
                        ),
                        exists().where(
                            DirectoryFavourite.directory_id == current_dir.id,
                            DirectoryFavourite.user_id == current_user_id,
                        ),
                    )
                )
            ).one()
            is_liked, is_favourited = bool(flags[0]), bool(flags[1])

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
    """Toggle a like atomically for one user/directory pair."""
    await acquire_reaction_toggle_lock(
        db,
        kind="directory-like",
        user_id=user_id,
        target_id=directory_id,
    )

    if await db.scalar(select(Directory.id).where(Directory.id == directory_id)) is None:
        raise NotFoundError("Directory not found")

    like = await db.scalar(
        select(DirectoryLike).where(
            DirectoryLike.user_id == user_id,
            DirectoryLike.directory_id == directory_id,
        )
    )

    if like is not None:
        await db.delete(like)
        await db.flush()
        await db.execute(
            update(Directory)
            .where(Directory.id == directory_id, Directory.like_count > 0)
            .values(like_count=Directory.like_count - 1)
        )
        return False

    db.add(DirectoryLike(id=uuid.uuid4(), user_id=user_id, directory_id=directory_id))
    await db.flush()
    await db.execute(
        update(Directory)
        .where(Directory.id == directory_id)
        .values(like_count=Directory.like_count + 1)
    )
    return True


async def set_directory_favourite(
    db: AsyncSession, user_id: uuid.UUID, directory_id: uuid.UUID, favourited: bool
) -> None:
    """Add or remove one user's favourite on a directory; repeating a call changes nothing."""
    await acquire_reaction_toggle_lock(
        db,
        kind="directory-favourite",
        user_id=user_id,
        target_id=directory_id,
    )

    if await db.scalar(select(Directory.id).where(Directory.id == directory_id)) is None:
        raise NotFoundError("Directory not found")

    result = await db.execute(
        select(DirectoryFavourite).where(
            DirectoryFavourite.user_id == user_id, DirectoryFavourite.directory_id == directory_id
        )
    )
    favourite = result.scalar_one_or_none()

    if favourite and not favourited:
        await db.delete(favourite)
    elif not favourite and favourited:
        db.add(DirectoryFavourite(id=uuid.uuid4(), user_id=user_id, directory_id=directory_id))

    await db.flush()


def _validate_zip_arcname(arcname: str) -> str:
    """Reject archive member names that can escape or change roots on extraction."""
    if not arcname or "\x00" in arcname or "\\" in arcname or arcname.startswith("/"):
        raise ValueError("Unsafe ZIP member path")

    parts = arcname.split("/")
    if any(not part for part in parts):
        raise ValueError("Unsafe ZIP member path")

    normalized_parts = [part.rstrip(" .") for part in parts]
    if any(part in {"", ".", ".."} for part in normalized_parts):
        raise ValueError("Unsafe ZIP member path")
    if re.match(r"^[A-Za-z]:", normalized_parts[0]):
        raise ValueError("Unsafe ZIP member drive prefix")
    return arcname


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
        arcname = _validate_zip_arcname(arcname)
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
