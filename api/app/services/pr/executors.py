"""Executors applying each forward PR operation."""

import typing
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.common.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.database.post_commit import PostCommitKey
from app.core.events.coalesce import JobKind
from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PullRequest
from app.models.tag import Tag
from app.services.pr.hierarchy import (
    _acquire_directory_tree_lock,
    _assert_directory_slug_available,
    _enqueue_deindex_directory_recursive,
    _enqueue_deindex_material_recursive,
    _enqueue_reindex_directory_recursive,
    _unique_directory_slug,
    _unique_material_slug,
    _validate_directory_parent,
)
from app.services.pr.operations import _resolve
from app.services.pr.versions import _make_version_for_file
from app.services.tag import get_or_create_tags, prune_orphan_tags


async def _exec_create_material(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    tags = p.get("tags", [])
    await get_or_create_tags(db, tags)
    normalized = [t.strip().lower() for t in tags if t.strip()]
    tag_objs = []
    if normalized:
        tag_result = await db.execute(select(Tag).where(Tag.name.in_(normalized)))
        tag_objs = list(tag_result.scalars().all())

    mat_id = uuid.uuid4()
    directory_id = _resolve(str(p.get("directory_id")) if p.get("directory_id") else None, id_map)
    if directory_id is not None:
        parent_dir = await db.scalar(
            select(Directory.id).where(Directory.id == directory_id, Directory.deleted_at.is_(None))
        )
        if parent_dir is None:
            raise NotFoundError("Parent directory not found")

    parent_material_id = _resolve(
        str(p["parent_material_id"]) if p.get("parent_material_id") else None, id_map
    )
    if parent_material_id is not None:
        parent_mat = await db.scalar(
            select(Material.id).where(
                Material.id == parent_material_id, Material.deleted_at.is_(None)
            )
        )
        if parent_mat is None:
            raise NotFoundError("Parent material not found")

    slug = await _unique_material_slug(db, directory_id, p["title"])
    m = Material(
        id=mat_id,
        directory_id=directory_id,
        title=p["title"],
        slug=slug,
        description=p.get("description"),
        type=p["type"],
        parent_material_id=parent_material_id,
        author_id=pr.author_id,
        is_anonymous=pr.is_anonymous,
        metadata_=p.get("metadata", {}),
        tags=tag_objs,
    )
    db.add(m)
    await db.flush()

    broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
    parent_topic = str(m.directory_id) if m.directory_id else "root"
    broadcasts.append((parent_topic, {"type": "child_added", "kind": "material", "id": str(m.id)}))

    if p.get("file_key"):
        await _make_version_for_file(
            db,
            file_key=str(p["file_key"]),
            payload=p,
            material_id=m.id,
            version_number=1,
            author_id=pr.author_id,
            pr_id=pr.id,
            is_anonymous=pr.is_anonymous,
        )

    for att in p.get("attachments") or []:
        att_tags = att.get("tags", [])
        await get_or_create_tags(db, att_tags)
        att_slug = await _unique_material_slug(db, None, att["title"])
        att_m = Material(
            id=uuid.uuid4(),
            directory_id=None,
            title=att["title"],
            slug=att_slug,
            type=att["type"],
            parent_material_id=m.id,
            author_id=pr.author_id,
            is_anonymous=pr.is_anonymous,
            tags=att_tags,
            metadata_=att.get("metadata", {}),
        )
        db.add(att_m)
        await db.flush()

        if att.get("file_key"):
            await _make_version_for_file(
                db,
                file_key=str(att["file_key"]),
                payload=att,
                material_id=att_m.id,
                version_number=1,
                author_id=pr.author_id,
                pr_id=pr.id,
                is_anonymous=pr.is_anonymous,
            )

    # External object metadata/copies are complete before entering the global
    # hierarchy critical section. Re-read the authoritative parents under the
    # transaction lock so a concurrent soft-delete cannot leave a live child.
    await _acquire_directory_tree_lock(db)
    if directory_id is not None:
        parent_dir = await db.scalar(
            select(Directory.id).where(Directory.id == directory_id, Directory.deleted_at.is_(None))
        )
        if parent_dir is None:
            raise NotFoundError("Parent directory not found")
    if parent_material_id is not None:
        parent_mat = await db.scalar(
            select(Material.id).where(
                Material.id == parent_material_id, Material.deleted_at.is_(None)
            )
        )
        if parent_mat is None:
            raise NotFoundError("Parent material not found")

    seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
    key = ("index_material", str(mat_id))
    if key not in seen:
        seen.add(key)
        db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_MATERIAL, mat_id))

    return mat_id


async def _exec_edit_material(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    mat_id = _resolve(str(p["material_id"]), id_map)
    mat = await db.scalar(
        select(Material).where(Material.id == mat_id).options(selectinload(Material.tags))
    )
    if not mat:
        raise NotFoundError("Material not found")

    if p.get("title") is not None:
        mat.title = p["title"]
        mat.slug = await _unique_material_slug(db, mat.directory_id, p["title"], exclude_id=mat.id)
    if p.get("type") is not None:
        mat.type = p["type"]
    if p.get("description") is not None:
        mat.description = p["description"]
    if p.get("tags") is not None:
        await get_or_create_tags(db, p["tags"])
        normalized = [t.strip().lower() for t in p["tags"] if t.strip()]
        tag_result = await db.execute(select(Tag).where(Tag.name.in_(normalized)))
        mat.tags = list(tag_result.scalars().all())
        await db.flush()
        await prune_orphan_tags(db)
    if p.get("metadata") is not None:
        mat.metadata_ = p["metadata"]

    if p.get("file_key"):
        # Optimistic locking: fetch the current head version before mutation.
        # Only enforced when the PR payload includes version_lock.
        _latest_mv = await db.scalar(
            select(MaterialVersion)
            .where(MaterialVersion.material_id == mat.id)
            .order_by(MaterialVersion.version_number.desc())
            .limit(1)
        )
        if "version_lock" in p and _latest_mv is not None:
            if _latest_mv.version_lock != p["version_lock"]:
                raise ConflictError(
                    f"Optimistic lock conflict on material {mat.id}: "
                    f"expected version_lock={p['version_lock']}, "
                    f"found {_latest_mv.version_lock}. "
                    "Another edit was applied after this PR was submitted."
                )

        _next_version_lock = (_latest_mv.version_lock + 1) if _latest_mv is not None else 0
        mat.current_version += 1

        await _make_version_for_file(
            db,
            file_key=str(p["file_key"]),
            payload=p,
            material_id=mat.id,
            version_number=mat.current_version,
            author_id=pr.author_id,
            pr_id=pr.id,
            is_anonymous=pr.is_anonymous,
            diff_summary=p.get("diff_summary"),
            version_lock=_next_version_lock,
        )

    parent_topic = str(mat.directory_id) if mat.directory_id else "root"
    broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
    broadcasts.append(
        (parent_topic, {"type": "child_updated", "kind": "material", "id": str(mat.id)})
    )
    # Also notify clients viewing the material directly so they drop any cached
    # file URL and re-fetch the new version's content.
    broadcasts.append((str(mat.id), {"type": "material_updated", "id": str(mat.id)}))

    seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
    key = ("index_material", str(mat.id))
    if key not in seen:
        seen.add(key)
        db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_MATERIAL, mat.id))

    return mat.id


async def _soft_delete_material_tree(db: AsyncSession, mat: Material) -> None:
    """Soft-delete a material and all its attachments."""
    now = datetime.now(UTC)
    mat.deleted_at = now

    broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
    broadcasts.append((str(mat.id), {"type": "material_deleted"}))
    parent_topic = str(mat.directory_id) if mat.directory_id else "root"
    broadcasts.append(
        (parent_topic, {"type": "child_removed", "kind": "material", "id": str(mat.id)})
    )

    versions = await db.scalars(
        select(MaterialVersion)
        .where(MaterialVersion.material_id == mat.id)
        .execution_options(include_deleted=True)
    )
    for v in versions:
        v.deleted_at = now

    att_mats = (
        await db.scalars(select(Material).where(Material.parent_material_id == mat.id))
    ).all()
    for att in att_mats:
        att.deleted_at = now
        broadcasts.append((str(att.id), {"type": "material_deleted"}))
        att_versions = await db.scalars(
            select(MaterialVersion)
            .where(MaterialVersion.material_id == att.id)
            .execution_options(include_deleted=True)
        )
        for av in att_versions:
            av.deleted_at = now


async def _exec_delete_material(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    await _acquire_directory_tree_lock(db)
    mat_id = _resolve(str(p["material_id"]), id_map)
    mat = await db.scalar(
        select(Material).where(Material.id == mat_id, Material.deleted_at.is_(None))
    )
    if not mat:
        raise NotFoundError("Material not found")

    deleted_id = mat.id

    await _enqueue_deindex_material_recursive(db, deleted_id)
    await _soft_delete_material_tree(db, mat)

    return deleted_id


async def _exec_create_directory(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    await _acquire_directory_tree_lock(db)
    tags = p.get("tags", [])
    tag_objs = []
    if tags:
        await get_or_create_tags(db, tags)
        normalized = [t.strip().lower() for t in tags if t.strip()]
        tag_result = await db.execute(select(Tag).where(Tag.name.in_(normalized)))
        tag_objs = list(tag_result.scalars().all())

    dir_id = uuid.uuid4()
    parent_id = _resolve(str(p["parent_id"]) if p.get("parent_id") else None, id_map)
    if parent_id is not None:
        parent_dir = await db.scalar(
            select(Directory.id).where(Directory.id == parent_id, Directory.deleted_at.is_(None))
        )
        if parent_dir is None:
            raise NotFoundError("Parent directory not found")
    dir_slug = await _unique_directory_slug(db, parent_id, p["name"])
    d = Directory(
        id=dir_id,
        name=p["name"],
        slug=dir_slug,
        type=p.get("type", "folder"),
        description=p.get("description"),
        parent_id=parent_id,
        tags=tag_objs,
        metadata_=p.get("metadata", {}),
        created_by=pr.author_id,
    )
    db.add(d)
    await db.flush()

    broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
    parent_topic = str(d.parent_id) if d.parent_id else "root"
    broadcasts.append((parent_topic, {"type": "child_added", "kind": "directory", "id": str(d.id)}))

    seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
    key = ("index_directory", str(d.id))
    if key not in seen:
        seen.add(key)
        db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_DIRECTORY, d.id))

    return dir_id


async def _exec_edit_directory(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    if p.get("name") is not None:
        await _acquire_directory_tree_lock(db)

    dir_id = _resolve(str(p["directory_id"]), id_map)
    dir_obj = await db.scalar(
        select(Directory).where(Directory.id == dir_id).options(selectinload(Directory.tags))
    )
    if not dir_obj:
        raise NotFoundError("Directory not found")

    name_or_slug_changed = p.get("name") is not None
    if p.get("name") is not None:
        dir_obj.name = p["name"]
        dir_obj.slug = await _unique_directory_slug(
            db, dir_obj.parent_id, p["name"], exclude_id=dir_obj.id
        )
    if p.get("type") is not None:
        dir_obj.type = p["type"]
    if p.get("description") is not None:
        dir_obj.description = p["description"]
    if p.get("tags") is not None:
        await get_or_create_tags(db, p["tags"])
        normalized = [t.strip().lower() for t in p["tags"] if t.strip()]
        tag_result = await db.execute(select(Tag).where(Tag.name.in_(normalized)))
        dir_obj.tags = list(tag_result.scalars().all())
    if p.get("metadata") is not None:
        dir_obj.metadata_ = p["metadata"]

    await db.flush()
    if p.get("tags") is not None:
        await prune_orphan_tags(db)

    if name_or_slug_changed:
        # Rename propagates ancestor_path to all descendants — reindex the whole subtree.
        await _enqueue_reindex_directory_recursive(db, dir_obj.id)
    else:
        seen: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
        key = ("index_directory", str(dir_obj.id))
        if key not in seen:
            seen.add(key)
            db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_DIRECTORY, dir_obj.id))

    return dir_obj.id


async def _soft_delete_directory_tree(db: AsyncSession, directory_id: uuid.UUID) -> None:
    """Soft-delete a directory and its entire subtree (children, materials, versions)."""
    now = datetime.now(UTC)

    dir_cte = (
        select(Directory.id)
        .where(Directory.id == directory_id)
        .cte(name="soft_del_dirs", recursive=True)
    )
    dir_alias = aliased(Directory)
    dir_cte = dir_cte.union_all(
        select(dir_alias.id)
        .join(dir_cte, dir_alias.parent_id == dir_cte.c.id)
        .where(dir_alias.deleted_at.is_(None))
    )
    all_dir_ids = (await db.scalars(select(dir_cte.c.id))).all()

    await db.execute(
        update(Directory)
        .where(Directory.id.in_(all_dir_ids), Directory.deleted_at.is_(None))
        .values(deleted_at=now)
    )
    broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
    for did in all_dir_ids:
        broadcasts.append((str(did), {"type": "directory_deleted"}))

    mat_rows = (
        await db.scalars(select(Material).where(Material.directory_id.in_(all_dir_ids)))
    ).all()
    for mat in mat_rows:
        await _soft_delete_material_tree(db, mat)


async def _exec_delete_directory(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    await _acquire_directory_tree_lock(db)
    dir_id = _resolve(str(p["directory_id"]), id_map)
    dir_obj = await db.scalar(
        select(Directory).where(Directory.id == dir_id, Directory.deleted_at.is_(None))
    )
    if not dir_obj:
        raise NotFoundError("Directory not found")

    deleted_id = dir_obj.id
    parent_id = dir_obj.parent_id

    await _enqueue_deindex_directory_recursive(db, deleted_id)
    await _soft_delete_directory_tree(db, deleted_id)

    parent_topic = str(parent_id) if parent_id else "root"
    db.info.setdefault(PostCommitKey.SSE, []).append(
        (parent_topic, {"type": "child_removed", "kind": "directory", "id": str(deleted_id)})
    )

    return deleted_id


async def _exec_move_item(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    target_id = _resolve(str(p["target_id"]), id_map)
    if target_id is None:
        raise BadRequestError("Move target is required")

    if p["target_type"] == "directory":
        await _acquire_directory_tree_lock(db)
        dir_obj = await db.scalar(select(Directory).where(Directory.id == target_id))
        if not dir_obj:
            raise NotFoundError("Directory not found")
        new_parent_id = _resolve(
            str(p["new_parent_id"]) if p.get("new_parent_id") else None, id_map
        )
        await _validate_directory_parent(db, target_id, new_parent_id)
        await _assert_directory_slug_available(
            db, new_parent_id, dir_obj.slug, exclude_id=dir_obj.id
        )
        dir_obj.parent_id = new_parent_id
        await db.flush()
        # Moving a directory changes ancestor_path for the whole subtree.
        await _enqueue_reindex_directory_recursive(db, dir_obj.id)
        return dir_obj.id
    else:
        await _acquire_directory_tree_lock(db)
        mat = await db.scalar(
            select(Material).where(Material.id == target_id, Material.deleted_at.is_(None))
        )
        if not mat:
            raise NotFoundError("Material not found")
        new_parent = _resolve(str(p["new_parent_id"]) if p.get("new_parent_id") else None, id_map)
        if new_parent is not None:
            parent_dir = await db.scalar(
                select(Directory.id).where(
                    Directory.id == new_parent, Directory.deleted_at.is_(None)
                )
            )
            if parent_dir is None:
                raise NotFoundError("Parent directory not found")
        mat.directory_id = new_parent
        # Re-slug to ensure uniqueness in new location
        mat.slug = await _unique_material_slug(db, new_parent, mat.title, exclude_id=mat.id)
        await db.flush()
        seen_jobs: set[tuple[str, str]] = db.info.setdefault(PostCommitKey.JOB_KEYS, set())
        key = ("index_material", str(mat.id))
        if key not in seen_jobs:
            seen_jobs.add(key)
            db.info.setdefault(PostCommitKey.JOBS, []).append((JobKind.INDEX_MATERIAL, mat.id))
        return mat.id


# Dispatch table
_EXECUTORS = {
    "create_material": _exec_create_material,
    "edit_material": _exec_edit_material,
    "delete_material": _exec_delete_material,
    "create_directory": _exec_create_directory,
    "edit_directory": _exec_edit_directory,
    "delete_directory": _exec_delete_directory,
    "move_item": _exec_move_item,
}


async def _capture_pre_state(
    db: AsyncSession,
    op_type: str,
    op: dict[str, typing.Any],
    id_map: dict[str, uuid.UUID],
) -> dict[str, typing.Any] | None:
    """Snapshot the entity state before mutation for revertable ops."""
    pre: dict[str, typing.Any] = {}

    if op_type == "edit_material":
        mat_id = _resolve(str(op["material_id"]), id_map)
        mat = await db.scalar(
            select(Material)
            .where(Material.id == mat_id)
            .options(selectinload(Material.tags))
            .with_for_update()
        )
        if not mat:
            return None
        pre = {
            "title": mat.title,
            "slug": mat.slug,
            "type": mat.type,
            "description": mat.description,
            "metadata": mat.metadata_,
            "tags": [t.name for t in mat.tags],
            "current_version": mat.current_version,
            "directory_id": str(mat.directory_id) if mat.directory_id else None,
        }
        if op.get("file_key"):
            latest_mv = await db.scalar(
                select(MaterialVersion)
                .where(MaterialVersion.material_id == mat.id)
                .order_by(MaterialVersion.version_number.desc())
                .limit(1)
            )
            if latest_mv:
                pre["prev_version_number"] = latest_mv.version_number
                pre["prev_file_key"] = latest_mv.file_key
                pre["prev_file_name"] = latest_mv.file_name
                pre["prev_cas_sha256"] = latest_mv.cas_sha256
        return pre

    if op_type == "edit_directory":
        dir_id = _resolve(str(op["directory_id"]), id_map)
        d = await db.scalar(
            select(Directory)
            .where(Directory.id == dir_id)
            .options(selectinload(Directory.tags))
            .with_for_update()
        )
        if not d:
            return None
        return {
            "name": d.name,
            "slug": d.slug,
            "type": d.type,
            "description": d.description,
            "metadata": d.metadata_,
            "tags": [t.name for t in d.tags],
            "parent_id": str(d.parent_id) if d.parent_id else None,
        }

    if op_type == "move_item":
        target_id = _resolve(str(op["target_id"]), id_map)
        if op["target_type"] == "directory":
            d = await db.scalar(
                select(Directory).where(Directory.id == target_id).with_for_update()
            )
            if d:
                return {
                    "target_type": "directory",
                    "prev_parent_id": str(d.parent_id) if d.parent_id else None,
                }
        else:
            mat = await db.scalar(
                select(Material).where(Material.id == target_id).with_for_update()
            )
            if mat:
                return {
                    "target_type": "material",
                    "prev_directory_id": str(mat.directory_id) if mat.directory_id else None,
                    "prev_slug": mat.slug,
                }

    return None
