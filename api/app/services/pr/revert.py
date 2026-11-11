"""Build and apply the revert of an applied pull request."""

import typing
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.core.common.exceptions import BadRequestError, NotFoundError
from app.core.database.post_commit import PostCommitKey
from app.core.events.coalesce import JobKind
from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PRStatus, PullRequest
from app.models.tag import Tag
from app.services.pr.executors import _exec_delete_directory, _exec_delete_material
from app.services.pr.hierarchy import (
    _acquire_directory_tree_lock,
    _assert_directory_restore_paths_available,
    _assert_directory_slug_available,
    _build_browse_path,
    _enqueue_reindex_directory_recursive,
    _unique_material_slug,
    _validate_directory_parent,
)
from app.services.pr.operations import _operation_uses_directory_tree_lock, _resolve
from app.services.tag import get_or_create_tags, prune_orphan_tags


async def _exec_undelete_material(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    """Restore a soft-deleted material and all its attachments."""
    await _acquire_directory_tree_lock(db)
    mat_id = _resolve(str(p["material_id"]), id_map)
    mat = await db.scalar(
        select(Material).where(Material.id == mat_id).execution_options(include_deleted=True)
    )
    if not mat:
        raise NotFoundError("Material not found (even among deleted)")

    if mat.directory_id is not None:
        parent_dir = await db.scalar(
            select(Directory.id).where(
                Directory.id == mat.directory_id, Directory.deleted_at.is_(None)
            )
        )
        if parent_dir is None:
            raise NotFoundError("Parent directory not found")
    if mat.parent_material_id is not None:
        parent_mat = await db.scalar(
            select(Material.id).where(
                Material.id == mat.parent_material_id, Material.deleted_at.is_(None)
            )
        )
        if parent_mat is None:
            raise NotFoundError("Parent material not found")

    mat.deleted_at = None

    versions = await db.scalars(
        select(MaterialVersion)
        .where(MaterialVersion.material_id == mat.id)
        .execution_options(include_deleted=True)
    )
    for v in versions:
        v.deleted_at = None

    att_mats = (
        await db.scalars(
            select(Material)
            .where(Material.parent_material_id == mat.id)
            .execution_options(include_deleted=True)
        )
    ).all()
    for att in att_mats:
        att.deleted_at = None
        att_vs = await db.scalars(
            select(MaterialVersion)
            .where(MaterialVersion.material_id == att.id)
            .execution_options(include_deleted=True)
        )
        for av in att_vs:
            av.deleted_at = None

    seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
    key = ("index_material", str(mat.id))
    if key not in seen:
        seen.add(key)
        db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_MATERIAL, mat.id))

    return mat.id


async def _exec_undelete_directory(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    """Restore a soft-deleted directory subtree without stealing a live path."""
    dir_id = _resolve(str(p["directory_id"]), id_map)
    if dir_id is None:
        raise BadRequestError("Directory ID is required")

    # Serialize against every application path that can allocate/change a
    # directory slug or parent.  The DB unique indexes remain authoritative,
    # while this lock lets us reject obsolete reverts before mutating the tree.
    await _acquire_directory_tree_lock(db)

    dir_cte = (
        select(Directory.id)
        .where(Directory.id == dir_id)
        .execution_options(include_deleted=True)
        .cte(name="undelete_dirs", recursive=True)
    )
    dir_alias = aliased(Directory)
    dir_cte = dir_cte.union_all(
        select(dir_alias.id)
        .where(dir_alias.parent_id == dir_cte.c.id)
        .execution_options(include_deleted=True)
    )
    all_dir_ids = list(
        (await db.scalars(select(dir_cte.c.id).execution_options(include_deleted=True))).all()
    )
    if not all_dir_ids:
        raise NotFoundError("Directory not found (even among deleted)")

    restore_rows = list(
        (
            await db.scalars(
                select(Directory)
                .where(Directory.id.in_(all_dir_ids))
                .order_by(Directory.id)
                .with_for_update()
                .execution_options(include_deleted=True)
            )
        ).all()
    )
    root_row = next((row for row in restore_rows if row.id == dir_id), None)
    if root_row is None:
        raise NotFoundError("Directory not found (even among deleted)")
    if root_row.parent_id is not None:
        live_parent = await db.scalar(
            select(Directory.id).where(
                Directory.id == root_row.parent_id, Directory.deleted_at.is_(None)
            )
        )
        if live_parent is None:
            raise NotFoundError("Parent directory not found")

    # Check every path before clearing any deleted_at value.  This covers both
    # the root NULL-parent hole and a child slug that was legitimately reused
    # under a surviving parent after the subtree was deleted.
    await _assert_directory_restore_paths_available(db, restore_rows)

    for directory in restore_rows:
        directory.deleted_at = None

    mat_rows = (
        await db.scalars(
            select(Material)
            .where(Material.directory_id.in_(all_dir_ids))
            .execution_options(include_deleted=True)
        )
    ).all()
    for mat in mat_rows:
        mat.deleted_at = None
        vs = await db.scalars(
            select(MaterialVersion)
            .where(MaterialVersion.material_id == mat.id)
            .execution_options(include_deleted=True)
        )
        for v in vs:
            v.deleted_at = None

    await _enqueue_reindex_directory_recursive(db, dir_id)
    return dir_id


async def _exec_revert_edit_material(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    """Revert a material to its pre-PR state using the captured pre_state snapshot."""
    mat_id = _resolve(str(p["material_id"]), id_map)
    pre = p.get("pre_state", {})
    mat = await db.scalar(
        select(Material).where(Material.id == mat_id).options(selectinload(Material.tags))
    )
    if not mat:
        raise NotFoundError("Material not found")

    mat.title = pre["title"]
    mat.slug = pre["slug"]
    mat.type = pre["type"]
    mat.description = pre.get("description")
    mat.metadata_ = pre.get("metadata", {})

    if "tags" in pre:
        await get_or_create_tags(db, pre["tags"])
        normalized = [t.strip().lower() for t in pre["tags"] if t.strip()]
        if normalized:
            tag_result = await db.execute(select(Tag).where(Tag.name.in_(normalized)))
            mat.tags = list(tag_result.scalars().all())
        else:
            mat.tags = []

    if pre.get("prev_version_number") is not None:
        versions_to_drop = await db.scalars(
            select(MaterialVersion).where(
                MaterialVersion.material_id == mat.id,
                MaterialVersion.version_number > pre["prev_version_number"],
            )
        )
        for v in versions_to_drop:
            if v.cas_sha256 and v.file_key and v.file_key.startswith("cas/"):
                db.info.setdefault(PostCommitKey.JOBS, []).append(
                    (
                        "release_cas_references",
                        [
                            {
                                "sha256": v.cas_sha256,
                                "operation_id": f"revert:version:{v.id}:release",
                            }
                        ],
                    )
                )
            await db.delete(v)

        mat.current_version = pre["prev_version_number"]

    await db.flush()
    if "tags" in pre:
        await prune_orphan_tags(db)

    seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
    key = ("index_material", str(mat.id))
    if key not in seen:
        seen.add(key)
        db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_MATERIAL, mat.id))

    return mat.id


async def _exec_revert_edit_directory(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    """Revert a directory to its pre-PR state."""
    await _acquire_directory_tree_lock(db)

    dir_id = _resolve(str(p["directory_id"]), id_map)
    pre = p.get("pre_state", {})
    d = await db.scalar(
        select(Directory).where(Directory.id == dir_id).options(selectinload(Directory.tags))
    )
    if not d:
        raise NotFoundError("Directory not found")

    name_changed = d.name != pre["name"]
    await _assert_directory_slug_available(db, d.parent_id, pre["slug"], exclude_id=d.id)
    d.name = pre["name"]
    d.slug = pre["slug"]
    d.type = pre["type"]
    d.description = pre.get("description")
    d.metadata_ = pre.get("metadata", {})

    if "tags" in pre:
        await get_or_create_tags(db, pre["tags"])
        normalized = [t.strip().lower() for t in pre["tags"] if t.strip()]
        if normalized:
            tag_result = await db.execute(select(Tag).where(Tag.name.in_(normalized)))
            d.tags = list(tag_result.scalars().all())
        else:
            d.tags = []

    await db.flush()
    if "tags" in pre:
        await prune_orphan_tags(db)

    if name_changed:
        await _enqueue_reindex_directory_recursive(db, d.id)
    else:
        seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
        key = ("index_directory", str(d.id))
        if key not in seen:
            seen.add(key)
            db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_DIRECTORY, d.id))

    return d.id


async def _exec_revert_move_item(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    """Move an item back to its pre-PR location."""
    target_id = _resolve(str(p["target_id"]), id_map)
    if target_id is None:
        raise BadRequestError("Revert move target is required")
    pre = p.get("pre_state", {})

    if pre.get("target_type") == "directory":
        await _acquire_directory_tree_lock(db)
        d = await db.scalar(select(Directory).where(Directory.id == target_id))
        if not d:
            raise NotFoundError("Directory not found")
        prev_parent = uuid.UUID(pre["prev_parent_id"]) if pre.get("prev_parent_id") else None
        await _validate_directory_parent(db, target_id, prev_parent)
        await _assert_directory_slug_available(db, prev_parent, d.slug, exclude_id=d.id)
        d.parent_id = prev_parent
        await db.flush()
        await _enqueue_reindex_directory_recursive(db, d.id)
        return d.id
    else:
        await _acquire_directory_tree_lock(db)
        mat = await db.scalar(
            select(Material).where(Material.id == target_id, Material.deleted_at.is_(None))
        )
        if not mat:
            raise NotFoundError("Material not found")
        prev_dir = uuid.UUID(pre["prev_directory_id"]) if pre.get("prev_directory_id") else None
        if prev_dir is not None:
            live_parent = await db.scalar(
                select(Directory.id).where(Directory.id == prev_dir, Directory.deleted_at.is_(None))
            )
            if live_parent is None:
                raise NotFoundError("Parent directory not found")
        mat.directory_id = prev_dir
        mat.slug = pre.get("prev_slug") or await _unique_material_slug(
            db, prev_dir, mat.title, exclude_id=mat.id
        )
        await db.flush()
        seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
        key = ("index_material", str(mat.id))
        if key not in seen:
            seen.add(key)
            db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_MATERIAL, mat.id))
        return mat.id


def _build_reverse_ops(applied_result: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
    """Build the list of reverse operations from an applied_result, in reverse order."""
    reverse_ops: list[dict[str, typing.Any]] = []

    for enriched in reversed(applied_result):
        op_type = str(enriched.get("op", ""))
        result_id = enriched.get("result_id")
        pre_state = enriched.get("pre_state")

        if op_type == "create_material":
            reverse_ops.append({"op": "delete_material", "material_id": result_id})
        elif op_type == "create_directory":
            reverse_ops.append({"op": "delete_directory", "directory_id": result_id})
        elif op_type == "edit_material":
            reverse_ops.append(
                {
                    "op": "edit_material",
                    "material_id": result_id,
                    "pre_state": pre_state,
                }
            )
        elif op_type == "edit_directory":
            reverse_ops.append(
                {
                    "op": "edit_directory",
                    "directory_id": result_id,
                    "pre_state": pre_state,
                }
            )
        elif op_type == "delete_material":
            reverse_ops.append({"op": "undelete_material", "material_id": result_id})
        elif op_type == "delete_directory":
            reverse_ops.append({"op": "undelete_directory", "directory_id": result_id})
        elif op_type == "move_item":
            reverse_ops.append(
                {
                    "op": "move_item",
                    "target_id": result_id,
                    "target_type": enriched.get("target_type"),
                    "pre_state": pre_state,
                }
            )

    return reverse_ops


# Dispatch table for revert ops — maps the REVERSE op name to its executor.
_REVERT_DISPATCH: dict[
    str,
    typing.Callable[
        [AsyncSession, dict[str, typing.Any], PullRequest, dict[str, uuid.UUID]],
        typing.Coroutine[typing.Any, typing.Any, uuid.UUID],
    ],
] = {
    "delete_material": _exec_delete_material,
    "delete_directory": _exec_delete_directory,
    "edit_material": _exec_revert_edit_material,
    "edit_directory": _exec_revert_edit_directory,
    "undelete_material": _exec_undelete_material,
    "undelete_directory": _exec_undelete_directory,
    "move_item": _exec_revert_move_item,
}


async def revert_pr(
    db: AsyncSession,
    original_pr: PullRequest,
    admin_user_id: uuid.UUID,
) -> PullRequest:
    """
    Create and immediately apply a revert PR that undoes all operations
    from the original PR. The original is marked as reverted.
    """
    if original_pr.applied_result is None:
        raise BadRequestError(
            "PR has no applied_result — cannot revert (legacy PR without pre-state snapshot)"
        )

    for enriched in original_pr.applied_result:
        op_type = str(enriched.get("op", ""))
        if op_type in ("edit_material", "edit_directory", "move_item") and not enriched.get(
            "pre_state"
        ):
            raise BadRequestError(
                f"Operation '{op_type}' in PR is missing pre_state snapshot — "
                "cannot revert (legacy PR approved before revert support was added)"
            )

    reverse_ops = _build_reverse_ops(original_pr.applied_result)

    revert = PullRequest(
        id=uuid.uuid4(),
        type="revert",
        status=PRStatus.APPROVED,
        title=f"Revert: {original_pr.title}",
        description=f'Automatic revert of PR "{original_pr.title}" (id: {original_pr.id})',
        payload=reverse_ops,
        summary_types=list({op["op"] for op in reverse_ops}),
        author_id=admin_user_id,
        reviewed_by=admin_user_id,
        reverts_pr_id=original_pr.id,
        approved_at=datetime.now(UTC),
    )
    db.add(revert)
    await db.flush()

    id_map: dict[str, uuid.UUID] = {}
    result_ops: list[dict[str, typing.Any]] = []

    # Reverts can also contain a non-hierarchy row-locking edit followed by a
    # hierarchy/namespace mutation.  Acquire the advisory lock before the first
    # reverse executor whenever any reverse operation needs it, preserving the
    # same advisory-lock -> row-lock ordering as normal PR application.
    if any(_operation_uses_directory_tree_lock(str(op.get("op") or ""), op) for op in reverse_ops):
        await _acquire_directory_tree_lock(db)

    for op in reverse_ops:
        op_type = str(op["op"])
        executor = _REVERT_DISPATCH.get(op_type)
        if not executor:
            raise BadRequestError(f"No revert executor for op type: {op_type}")

        result_id = await executor(db, op, revert, id_map)

        enriched_rev: dict[str, typing.Any] = dict(op)
        enriched_rev["result_id"] = str(result_id)
        bp = await _build_browse_path(db, op_type, result_id)
        if bp:
            enriched_rev["result_browse_path"] = bp
        result_ops.append(enriched_rev)

    revert.applied_result = result_ops
    flag_modified(revert, "applied_result")

    original_pr.reverted_by_pr_id = revert.id

    return revert
