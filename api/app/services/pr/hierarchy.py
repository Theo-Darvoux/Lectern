"""Directory-tree locking, slug allocation, browse paths and search reindexing
shared by PR application and revert."""

import re
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.core.common.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.database.post_commit import PostCommitKey
from app.core.events.coalesce import JobKind
from app.models.directory import Directory
from app.models.material import Material
from app.services.directory import slugify

# All directory-parent mutations take this transaction-level lock on PostgreSQL.
# Row locks cannot prevent two independent directory rows from being moved below
# one another concurrently, so the tree invariant needs a shared serialization
# point.  The value is arbitrary but stable (ASCII "WIKINT").
_DIRECTORY_TREE_LOCK_KEY = 0x57494B494E54


async def _acquire_directory_tree_lock(db: AsyncSession) -> None:
    """Serialize directory hierarchy and live namespace mutation on PostgreSQL."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _DIRECTORY_TREE_LOCK_KEY},
        )


async def _validate_directory_parent(
    db: AsyncSession, target_id: uuid.UUID, new_parent_id: uuid.UUID | None
) -> None:
    """Require a present parent and preserve an acyclic directory hierarchy."""
    if new_parent_id == target_id:
        raise BadRequestError("Cannot move a directory into itself")

    check_id = new_parent_id
    seen: set[uuid.UUID] = set()
    while check_id is not None:
        if check_id == target_id:
            raise BadRequestError("Cannot move a directory into one of its own descendants")
        if check_id in seen:
            raise BadRequestError("Cannot move a directory below an existing circular hierarchy")
        seen.add(check_id)

        row = (
            await db.execute(
                select(Directory.id, Directory.parent_id).where(
                    Directory.id == check_id, Directory.deleted_at.is_(None)
                )
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Parent directory not found")
        check_id = row.parent_id


def _slug_pattern(base: str) -> re.Pattern[str]:
    """Compile a pattern matching exactly `base` or `base-<digits>`."""
    return re.compile(r"^" + re.escape(base) + r"(?:-\d+)?$")


async def _unique_material_slug(
    db: AsyncSession,
    directory_id: uuid.UUID | None,
    title: str,
    exclude_id: uuid.UUID | None = None,
) -> str:
    """Generate a slug unique within the directory, appending -2, -3, … on collision.

    Uses SELECT ... FOR UPDATE to prevent race conditions between concurrent
    PR applications. Post-filters the LIKE results to only count exact matches
    (`base` or `base-<digits>`), avoiding false collisions with slugs that
    merely share a prefix (e.g. `linear-algebra` vs `linear-algebra-notes`).
    """
    base = slugify(title) or "untitled"
    pattern = _slug_pattern(base)

    stmt = (
        select(Material.slug)
        .where((Material.slug == base) | Material.slug.like(f"{base}-%"))
        .with_for_update()
    )
    if directory_id is None:
        stmt = stmt.where(Material.directory_id.is_(None))
    else:
        stmt = stmt.where(Material.directory_id == directory_id)

    if exclude_id:
        stmt = stmt.where(Material.id != exclude_id)

    result = await db.execute(stmt)
    # Only count slugs that are exactly `base` or `base-N` (digits only).
    existing = {s for s in result.scalars().all() if pattern.match(s)}

    if base not in existing:
        return base
    for i in range(2, len(existing) + 100):
        candidate = f"{base}-{i}"
        if candidate not in existing:
            return candidate
    return f"{base}-{uuid.uuid4().hex[:8]}"


async def _assert_directory_slug_available(
    db: AsyncSession,
    parent_id: uuid.UUID | None,
    slug: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Reject a live sibling/root slug collision under the namespace lock.

    PostgreSQL's partial unique indexes remain the final authority.  The shared
    transaction-level lock makes application create/move/restore schedules
    deterministic even when there is no existing row available to lock.
    """
    await _acquire_directory_tree_lock(db)

    stmt = select(Directory.id).where(Directory.slug == slug, Directory.deleted_at.is_(None))
    if parent_id is None:
        stmt = stmt.where(Directory.parent_id.is_(None))
    else:
        stmt = stmt.where(Directory.parent_id == parent_id)
    if exclude_id is not None:
        stmt = stmt.where(Directory.id != exclude_id)

    if await db.scalar(stmt.limit(1)) is not None:
        raise ConflictError(
            f'Directory path "{slug}" is already in use at this location; reload and retry'
        )


async def _assert_directory_restore_paths_available(
    db: AsyncSession, restore_rows: list[Directory]
) -> None:
    """Preflight an entire undelete subtree against the current live namespace."""
    await _acquire_directory_tree_lock(db)

    restoring_ids = {directory.id for directory in restore_rows}
    restoring_keys: set[tuple[uuid.UUID | None, str]] = set()
    for directory in restore_rows:
        key = (directory.parent_id, directory.slug)
        if key in restoring_keys:
            raise ConflictError(
                "Cannot restore directory subtree because it contains duplicate paths"
            )
        restoring_keys.add(key)

    if not restoring_keys:
        return

    slugs = {slug for _parent_id, slug in restoring_keys}
    parent_ids = {parent_id for parent_id, _slug in restoring_keys if parent_id is not None}
    parent_scope: ColumnElement[bool] = Directory.parent_id.in_(parent_ids)
    if any(parent_id is None for parent_id, _slug in restoring_keys):
        parent_scope = parent_scope | Directory.parent_id.is_(None)

    live_rows = await db.execute(
        select(Directory.parent_id, Directory.slug).where(
            Directory.deleted_at.is_(None),
            Directory.id.not_in(restoring_ids),
            Directory.slug.in_(slugs),
            parent_scope,
        )
    )
    live_keys = {(row.parent_id, row.slug) for row in live_rows}
    conflicts = restoring_keys & live_keys
    if conflicts:
        _parent_id, slug = sorted(conflicts, key=lambda item: (str(item[0]), item[1]))[0]
        raise ConflictError(
            f'Directory path "{slug}" is already in use at this location; reload and retry'
        )


async def _unique_directory_slug(
    db: AsyncSession,
    parent_id: uuid.UUID | None,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> str:
    """Generate a slug unique among sibling directories.

    The transaction-level directory namespace lock closes the empty-result race
    that SELECT ... FOR UPDATE alone cannot serialize. Same prefix-collision fix
    as _unique_material_slug.
    """
    await _acquire_directory_tree_lock(db)

    base = slugify(name) or "untitled"
    pattern = _slug_pattern(base)

    stmt = (
        select(Directory.slug)
        .where((Directory.slug == base) | Directory.slug.like(f"{base}-%"))
        .with_for_update()
    )
    if parent_id is None:
        stmt = stmt.where(Directory.parent_id.is_(None))
    else:
        stmt = stmt.where(Directory.parent_id == parent_id)

    if exclude_id:
        stmt = stmt.where(Directory.id != exclude_id)

    result = await db.execute(stmt)
    existing = {s for s in result.scalars().all() if pattern.match(s)}

    if base not in existing:
        return base
    for i in range(2, len(existing) + 100):
        candidate = f"{base}-{i}"
        if candidate not in existing:
            return candidate
    return f"{base}-{uuid.uuid4().hex[:8]}"


async def _enqueue_deindex_material_recursive(db: AsyncSession, material_id: uuid.UUID) -> None:
    """Recursively find a material and all its attachments and enqueue de-indexing."""
    mat_cte = (
        select(Material.id)
        .where(Material.id == material_id)
        .cte(name="descendant_mats", recursive=True)
    )
    mat_alias = aliased(Material)
    mat_cte = mat_cte.union_all(
        select(mat_alias.id).join(mat_cte, mat_alias.parent_material_id == mat_cte.c.id)
    )

    all_mat_ids = (await db.scalars(select(mat_cte.c.id))).all()

    for mid in all_mat_ids:
        db.info.setdefault(PostCommitKey.JOBS, []).append(
            ("delete_indexed_item", "materials", str(mid))
        )


async def _enqueue_reindex_directory_recursive(db: AsyncSession, directory_id: uuid.UUID) -> None:
    """Enqueue index_directory for a directory and all its descendants, plus their materials."""
    dir_cte = (
        select(Directory.id)
        .where(Directory.id == directory_id)
        .cte(name="reindex_descendant_dirs", recursive=True)
    )
    dir_alias = aliased(Directory)
    dir_cte = dir_cte.union_all(
        select(dir_alias.id).join(dir_cte, dir_alias.parent_id == dir_cte.c.id)
    )

    all_dir_ids = (await db.scalars(select(dir_cte.c.id))).all()

    mat_ids = (
        await db.scalars(select(Material.id).where(Material.directory_id.in_(all_dir_ids)))
    ).all()

    seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())

    for did in all_dir_ids:
        key = ("index_directory", str(did))
        if key not in seen:
            seen.add(key)
            db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_DIRECTORY, did))

    for mid in mat_ids:
        key = ("index_material", str(mid))
        if key not in seen:
            seen.add(key)
            db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_MATERIAL, mid))


async def _enqueue_deindex_directory_recursive(db: AsyncSession, directory_id: uuid.UUID) -> None:
    """Recursively find all subdirectories and materials (including attachments) to de-index them."""
    # 1. Find all descendant directories (recursive subfolders)
    dir_cte = (
        select(Directory.id)
        .where(Directory.id == directory_id)
        .cte(name="descendant_dirs", recursive=True)
    )
    dir_alias = aliased(Directory)
    dir_cte = dir_cte.union_all(
        select(dir_alias.id).join(dir_cte, dir_alias.parent_id == dir_cte.c.id)
    )

    all_dir_ids = (await db.scalars(select(dir_cte.c.id))).all()

    # 2. Find all materials directly in these directories
    mat_ids = (
        await db.scalars(select(Material.id).where(Material.directory_id.in_(all_dir_ids)))
    ).all()

    # 3. For each material, perform recursive de-indexing (handles attachments in system dirs)
    for mid in mat_ids:
        await _enqueue_deindex_material_recursive(db, mid)

    # 4. Enqueue the directories themselves (dedup via set)
    seen_deindex: set[tuple[str, str, str]] = db.info.setdefault(PostCommitKey.DEINDEX_KEYS, set())
    for did in all_dir_ids:
        key = ("delete_indexed_item", "directories", str(did))
        if key not in seen_deindex:
            seen_deindex.add(key)
            db.info.setdefault(PostCommitKey.JOBS, []).append(key)


async def _material_browse_path(db: AsyncSession, material_id: uuid.UUID) -> str | None:
    from app.services.directory import get_directory_path

    mat = await db.scalar(select(Material).where(Material.id == material_id))
    if not mat:
        return None
    slugs = []
    if mat.directory_id is not None:
        slugs = [p["slug"] for p in await get_directory_path(db, mat.directory_id)]
    slugs.append(mat.slug)
    return "/".join(slugs)


async def _directory_browse_path(db: AsyncSession, directory_id: uuid.UUID) -> str | None:
    from app.services.directory import get_directory_path

    path_parts = await get_directory_path(db, directory_id)
    return "/".join(p["slug"] for p in path_parts) if path_parts else None


async def _build_browse_path(db: AsyncSession, op_type: str, result_id: uuid.UUID) -> str | None:
    """Build the slug-based browse path for a result item."""
    if "directory" in op_type:
        return await _directory_browse_path(db, result_id)
    if "material" in op_type:
        return await _material_browse_path(db, result_id)
    if op_type == "move_item":
        # A move targets either kind; the id tells which.
        if await db.scalar(select(Directory.id).where(Directory.id == result_id)):
            return await _directory_browse_path(db, result_id)
        return await _material_browse_path(db, result_id)
    return None
