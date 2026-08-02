import asyncio
import contextlib
import logging
import re
import typing
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.core.common.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.database.post_commit import PostCommitKey
from app.core.events.coalesce import JobKind
from app.core.storage.facade import copy_object, get_object_info, object_exists
from app.models.cas_staging_claim import CasStagingClaim
from app.models.directory import Directory
from app.models.material import Material, MaterialVersion
from app.models.pull_request import PRFileClaim, PRStatus, PullRequest
from app.models.security import VirusScanResult
from app.models.tag import Tag
from app.models.upload import Upload
from app.models.user import User
from app.schemas.pull_request import PullRequestCreate
from app.services.directory import slugify
from app.services.notification import notify_user
from app.services.tag import get_or_create_tags, prune_orphan_tags

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_temp_id(value: str | None) -> bool:
    """Check if a value is a $-prefixed temporary inter-reference ID."""
    return isinstance(value, str) and value.startswith("$")


def _pr_directory_topics(payload: list[dict[str, typing.Any]]) -> set[str]:
    """Return the set of SSE topic strings for all real directories targeted by a PR payload.

    create_material ops use ``directory_id``; create_directory ops use ``parent_id``.
    Null → "root".  $temp-id strings are skipped (directory not yet resolved).
    """
    topics: set[str] = set()
    for op in payload:
        raw = op.get("directory_id") or op.get("parent_id")
        if raw is None:
            topics.add("root")
        elif isinstance(raw, str) and not raw.startswith("$"):
            topics.add(raw)
        elif not isinstance(raw, str):
            topics.add(str(raw))
    return topics


def _resolve(value: str | None, id_map: dict[str, uuid.UUID]) -> uuid.UUID | None:
    """Resolve a value that may be a temp ID, a real UUID string, or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if _is_temp_id(s):
        resolved = id_map.get(s)
        if resolved is None:
            raise BadRequestError(f"Unresolved temp_id reference: {s}")
        return resolved
    try:
        return uuid.UUID(s)
    except ValueError:
        raise BadRequestError(f"Invalid UUID: {s}")


def _collect_temp_refs(op: dict[str, typing.Any]) -> set[str]:
    """Collect all $-prefixed references from an operation dict (not its own temp_id)."""
    refs: set[str] = set()
    for key, val in op.items():
        if key == "temp_id":
            continue
        if isinstance(val, str) and _is_temp_id(val):
            refs.add(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    for v2 in item.values():
                        if isinstance(v2, str) and _is_temp_id(v2):
                            refs.add(v2)
    return refs


def get_pr_staging_files(pr: PullRequest) -> list[str]:
    """Collect all 'uploads/' prefixed file keys from a PR payload."""
    staging_files: list[str] = []
    for op in pr.payload:
        fk = op.get("file_key")
        if fk and str(fk).startswith("uploads/"):
            staging_files.append(str(fk))
        attachments = op.get("attachments")
        if isinstance(attachments, list):
            for att in attachments:
                att_fk = att.get("file_key") if isinstance(att, dict) else None
                if att_fk and str(att_fk).startswith("uploads/"):
                    staging_files.append(str(att_fk))
    return staging_files


def get_pr_all_file_keys(pr: PullRequest) -> list[str]:
    """Collect all file keys from a PR payload, regardless of prefix."""
    keys: list[str] = []
    for op in pr.payload:
        fk = op.get("file_key")
        if fk:
            keys.append(str(fk))
        attachments = op.get("attachments")
        if isinstance(attachments, list):
            for att in attachments:
                att_fk = att.get("file_key") if isinstance(att, dict) else None
                if att_fk:
                    keys.append(str(att_fk))
    return keys


async def _release_pr_upload_quota(
    db: AsyncSession,
    pr: PullRequest,
    redis: Redis,
    approved: bool = False,  # type: ignore[type-arg]
) -> None:
    """Find all uploads associated with the PR and remove them from the user's Redis quota.

    If ``approved`` is True, also updates the Upload row status to 'applied'.
    """
    keys = get_pr_all_file_keys(pr)
    if not keys:
        return

    # Find uploads by their quarantine or final keys
    stmt = select(Upload).where((Upload.quarantine_key.in_(keys)) | (Upload.final_key.in_(keys)))
    result = await db.execute(stmt)
    uploads = list(result.scalars().all())

    if not uploads:
        return

    from app.routers.upload.helpers import _QUOTA_KEY_PREFIX

    quota_key = f"{_QUOTA_KEY_PREFIX}{pr.author_id}"
    keys_to_remove: list[str] = []

    for upload in uploads:
        if upload.quarantine_key:
            keys_to_remove.append(upload.quarantine_key)
        keys_to_remove.append(f"staging:{upload.user_id}:{upload.upload_id}")

        if approved:
            upload.status = "applied"

    if keys_to_remove:
        try:
            await redis.zrem(quota_key, *keys_to_remove)
        except Exception as exc:
            logger.warning("Failed to release upload quota for PR %s: %s", pr.id, exc)


async def _cleanup_pr_resources(
    db: AsyncSession,
    pr: PullRequest,
    delete_staging: bool = False,
    redis: Redis | None = None,  # type: ignore[type-arg]
) -> None:
    """Release file claims and optionally schedule deletion of staging files."""
    await db.execute(delete(PRFileClaim).where(PRFileClaim.pr_id == pr.id))

    # Release upload quota slots immediately
    if redis is None:
        from app.core.database.redis import redis_client

        redis = redis_client

    if redis:  # type: ignore[truthy-bool]
        # If delete_staging is False, it's likely an approval
        await _release_pr_upload_quota(db, pr, redis, approved=not delete_staging)

    # CAS staging refs need their original SHA-256; an opaque cas/<hmac> key is
    # intentionally insufficient. Every PR outcome releases its staging owner:
    # approval first creates the MaterialVersion owner, while rejection does not.
    cas_payload_refs: dict[str, str] = {}
    for op in pr.payload:
        file_key = op.get("file_key")
        content_sha = op.get("content_sha256")
        if file_key and str(file_key).startswith("cas/") and content_sha:
            cas_payload_refs[str(file_key)] = str(content_sha)
        attachments = op.get("attachments")
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            attachment_key = attachment.get("file_key")
            attachment_sha = attachment.get("content_sha256")
            if attachment_key and str(attachment_key).startswith("cas/") and attachment_sha:
                cas_payload_refs[str(attachment_key)] = str(attachment_sha)

    if cas_payload_refs:
        upload_rows = list(
            (
                await db.scalars(
                    select(Upload).where(Upload.final_key.in_(cas_payload_refs))
                )
            ).all()
        )
        uploads_by_key = {row.final_key: row for row in upload_rows if row.final_key}
        refs_to_release: list[str] = []
        for key, payload_sha in cas_payload_refs.items():
            upload = uploads_by_key.get(key)
            if upload is None:
                refs_to_release.append(payload_sha)
            elif upload.cas_ref_count > 0:
                reference_sha = upload.content_sha256 or upload.sha256
                if reference_sha:
                    refs_to_release.append(reference_sha)
                upload.cas_ref_count = 0
                if delete_staging:
                    upload.status = "failed"
        if refs_to_release:
            db.info.setdefault(PostCommitKey.JOBS, []).append(
                (
                    "release_cas_references",
                    [
                        {
                            "sha256": sha256,
                            "operation_id": f"pr:{pr.id}:staging:{sha256}:release",
                        }
                        for sha256 in sorted(set(refs_to_release))
                    ],
                )
            )

    if delete_staging:
        uploads_to_delete = get_pr_staging_files(pr)
        if uploads_to_delete:
            db.info.setdefault(PostCommitKey.JOBS, []).append(
                ("delete_storage_objects", uploads_to_delete)
            )


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


async def _unique_directory_slug(
    db: AsyncSession,
    parent_id: uuid.UUID | None,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> str:
    """Generate a slug unique among sibling directories.

    Uses SELECT ... FOR UPDATE to prevent race conditions. Same prefix-collision
    fix as _unique_material_slug.
    """
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


def topo_sort_operations(operations: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
    """
    Topologically sort operations so that any op defining a temp_id
    comes before ops referencing that temp_id.  Preserves submission
    order when there are no dependencies.
    """
    n = len(operations)
    # Build mapping: temp_id -> index of the defining op
    definer: dict[str, int] = {}
    for i, op in enumerate(operations):
        tid = op.get("temp_id")
        if tid:
            definer[tid] = i

    # Build adjacency: edges[i] = set of indices that must come before i
    deps: dict[int, set[int]] = defaultdict(set)
    for i, op in enumerate(operations):
        for ref in _collect_temp_refs(op):
            if ref in definer:
                deps[i].add(definer[ref])

    # Kahn's algorithm maintaining original order via index-based BFS
    in_degree = [0] * n
    for i in range(n):
        in_degree[i] = len(deps[i])

    queue: list[int] = [i for i in range(n) if in_degree[i] == 0]
    result: list[int] = []

    while queue:
        # Take the item with the lowest original index (stable order)
        queue.sort()
        idx = queue.pop(0)
        result.append(idx)
        # Release dependents
        for j in range(n):
            if idx in deps[j]:
                deps[j].discard(idx)
                in_degree[j] -= 1
                if in_degree[j] == 0:
                    queue.append(j)

    if len(result) != n:
        raise BadRequestError("Cyclic dependency detected among temp_id references")

    return [operations[i] for i in result]


def _resolve_mime_type(
    file_key: str, payload: dict[str, typing.Any], s3_mime: str | None = None
) -> str:
    """Determine MIME type. Trusts S3 metadata or payload hint over re-scanning."""
    if s3_mime and s3_mime != "application/octet-stream":
        return s3_mime
    return payload.get("file_mime_type") or "application/octet-stream"


async def _resolve_thumbnail_info(
    db: AsyncSession, cas_file_key: str
) -> tuple[str | None, str | None]:
    """Return (thumbnail_key, thumbnail_status) from the uploads table for the given CAS key."""
    from app.models.upload import Upload as _Upload

    row = await db.execute(
        select(_Upload.thumbnail_key, _Upload.thumbnail_status)
        .where(_Upload.final_key == cas_file_key)
        .order_by(_Upload.updated_at.desc())
        .limit(1)
    )
    result = row.one_or_none()
    if result is None:
        return None, None
    return result[0], result[1]


async def _make_version_for_file(
    db: AsyncSession,
    *,
    file_key: str,
    payload: dict[str, typing.Any],
    material_id: uuid.UUID,
    version_number: int,
    author_id: uuid.UUID | None,
    pr_id: uuid.UUID,
    diff_summary: str | None = None,
    version_lock: int = 0,
) -> MaterialVersion:
    """Resolve file metadata and create+flush a MaterialVersion.

    Handles CAS V2 (cas/ prefix) and legacy V1 (uploads/ prefix) keys.
    V1 keys are copied to the materials/ prefix and scheduled for deletion.
    CAS keys get their content-addressed ref incremented.
    """
    original_sha256: str | None = None
    if file_key.startswith("cas/"):
        upload_row = (
            await db.execute(
                select(Upload.size_bytes, Upload.content_sha256)
            .where(Upload.final_key == file_key)
            .order_by(Upload.updated_at.desc())
            .limit(1)
            )
        ).one_or_none()
        upload_size = upload_row[0] if upload_row is not None else None
        stored_content_sha256 = upload_row[1] if upload_row is not None else None
        real_size = upload_size if upload_size is not None else (payload.get("file_size") or 0)
        mime_type = payload.get("file_mime_type") or "application/octet-stream"
        thumbnail_key, thumbnail_status = await _resolve_thumbnail_info(db, file_key)
    else:
        info = await get_object_info(file_key)
        real_size = info["size"]
        mime_type = _resolve_mime_type(file_key, payload, s3_mime=info.get("content_type"))
        new_key = file_key.replace("uploads/", "materials/", 1)
        await copy_object(file_key, new_key)
        db.info.setdefault(PostCommitKey.JOBS, []).append(("delete_storage_objects", [file_key]))
        file_key = new_key
        thumbnail_key = None
        thumbnail_status = None

    mv = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material_id,
        version_number=version_number,
        file_key=file_key,
        file_name=payload.get("file_name"),
        file_size=real_size,
        file_mime_type=mime_type,
        # CAS object keys are derived from the original upload hash. The
        # post-sanitization content hash is useful for integrity, but is not the
        # reference-count key when the two differ.
        cas_sha256=(stored_content_sha256 if file_key.startswith("cas/") else None)
        or payload.get("content_sha256"),
        author_id=author_id,
        pr_id=pr_id,
        virus_scan_result=VirusScanResult.CLEAN,
        version_lock=version_lock,
        diff_summary=diff_summary,
        thumbnail_key=thumbnail_key,
        thumbnail_status=thumbnail_status,
    )
    db.add(mv)
    await db.flush()

    if mv.file_key and mv.file_key.startswith("cas/") and mv.cas_sha256:
        db.info.setdefault(PostCommitKey.JOBS, []).append(
            (
                "add_cas_references",
                [
                    {
                        "sha256": mv.cas_sha256,
                        "operation_id": f"pr:{pr_id}:version:{mv.id}:add",
                        "initial_data": {
                            "final_key": mv.file_key,
                            "size": mv.file_size,
                            "mime_type": mv.file_mime_type,
                            "file_name": mv.file_name,
                        },
                    }
                ],
            )
        )

    return mv


# ---------------------------------------------------------------------------
# Individual operation executors
# ---------------------------------------------------------------------------


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

    slug = await _unique_material_slug(db, directory_id, p["title"])
    m = Material(
        id=mat_id,
        directory_id=directory_id,
        title=p["title"],
        slug=slug,
        description=p.get("description"),
        type=p["type"],
        parent_material_id=_resolve(
            str(p["parent_material_id"]) if p.get("parent_material_id") else None,
            id_map,
        ),
        author_id=pr.author_id,
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
            )

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
    mat_id = _resolve(str(p["material_id"]), id_map)
    mat = await db.scalar(select(Material).where(Material.id == mat_id))
    if not mat:
        raise NotFoundError("Material not found")

    deleted_id = mat.id

    await _enqueue_deindex_material_recursive(db, deleted_id)
    await _soft_delete_material_tree(db, mat)

    return deleted_id


async def _exec_create_directory(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    tags = p.get("tags", [])
    tag_objs = []
    if tags:
        await get_or_create_tags(db, tags)
        normalized = [t.strip().lower() for t in tags if t.strip()]
        tag_result = await db.execute(select(Tag).where(Tag.name.in_(normalized)))
        tag_objs = list(tag_result.scalars().all())

    dir_id = uuid.uuid4()
    parent_id = _resolve(str(p["parent_id"]) if p.get("parent_id") else None, id_map)
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
    dir_id = _resolve(str(p["directory_id"]), id_map)
    dir_obj = await db.scalar(select(Directory).where(Directory.id == dir_id))
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

    if p["target_type"] == "directory":
        dir_obj = await db.scalar(select(Directory).where(Directory.id == target_id))
        if not dir_obj:
            raise NotFoundError("Directory not found")
        new_parent_id = _resolve(
            str(p["new_parent_id"]) if p.get("new_parent_id") else None, id_map
        )
        # Reject self-move and circular ancestry
        if new_parent_id == target_id:
            raise BadRequestError("Cannot move a directory into itself")
        if new_parent_id is not None:
            # Walk up from new_parent to ensure target is not an ancestor
            check_id: uuid.UUID | None = new_parent_id
            seen: set[uuid.UUID] = set()
            while check_id:
                if check_id == target_id:
                    raise BadRequestError("Cannot move a directory into one of its own descendants")
                if check_id in seen:
                    break  # existing circular chain — stop
                seen.add(check_id)
                parent = await db.scalar(
                    select(Directory.parent_id).where(Directory.id == check_id)
                )
                check_id = parent
        dir_obj.parent_id = new_parent_id
        await db.flush()
        # Moving a directory changes ancestor_path for the whole subtree.
        await _enqueue_reindex_directory_recursive(db, dir_obj.id)
        return dir_obj.id
    else:
        mat = await db.scalar(select(Material).where(Material.id == target_id))
        if not mat:
            raise NotFoundError("Material not found")
        new_parent = _resolve(str(p["new_parent_id"]) if p.get("new_parent_id") else None, id_map)
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


# ---------------------------------------------------------------------------
# Browse-path resolution (for post-approval links)
# ---------------------------------------------------------------------------


async def _build_browse_path(db: AsyncSession, op_type: str, result_id: uuid.UUID) -> str | None:
    """Build the slug-based browse path for a result item."""
    from app.services.directory import get_directory_path

    if "directory" in op_type:
        path_parts = await get_directory_path(db, result_id)
        if path_parts:
            return "/".join(p["slug"] for p in path_parts)
        return None

    if "material" in op_type:
        mat = await db.scalar(select(Material).where(Material.id == result_id))
        if not mat:
            return None
        if mat.directory_id is None:
            return mat.slug
        dir_parts = await get_directory_path(db, mat.directory_id)
        slugs = [p["slug"] for p in dir_parts]
        slugs.append(mat.slug)
        return "/".join(slugs)

    if op_type == "move_item":
        # Could be either; try directory first, then material
        d = await db.scalar(select(Directory).where(Directory.id == result_id))
        if d:
            path_parts = await get_directory_path(db, result_id)
            return "/".join(p["slug"] for p in path_parts) if path_parts else ""

        mat = await db.scalar(select(Material).where(Material.id == result_id))
        if mat:
            if mat.directory_id is None:
                return typing.cast(str, mat.slug)
            dir_parts = await get_directory_path(db, mat.directory_id)
            slugs = [p["slug"] for p in dir_parts]
            slugs.append(mat.slug)
            return "/".join(slugs)

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


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
            select(Material).where(Material.id == mat_id).options(selectinload(Material.tags))
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
            select(Directory).where(Directory.id == dir_id).options(selectinload(Directory.tags))
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
            d = await db.scalar(select(Directory).where(Directory.id == target_id))
            if d:
                return {
                    "target_type": "directory",
                    "prev_parent_id": str(d.parent_id) if d.parent_id else None,
                }
        else:
            mat = await db.scalar(select(Material).where(Material.id == target_id))
            if mat:
                return {
                    "target_type": "material",
                    "prev_directory_id": str(mat.directory_id) if mat.directory_id else None,
                    "prev_slug": mat.slug,
                }

    return None


async def create_pull_request_service(
    db: AsyncSession,
    data: PullRequestCreate,
    current_user: User,
    redis: Redis | None = None,  # type: ignore[type-arg]
) -> PullRequest:
    """Validate and create a new batch pull request."""
    is_privileged = current_user.is_moderator

    if not is_privileged:
        if len(data.operations) > settings.pr_max_ops_student:
            raise BadRequestError(
                f"You can include at most {settings.pr_max_ops_student} changes per contribution"
            )
        for op in data.operations:
            if (
                getattr(op, "op", None) == "create_material"
                and len(getattr(op, "attachments", [])) > settings.pr_max_attachments_per_material
            ):
                raise BadRequestError(
                    f"You can add at most {settings.pr_max_attachments_per_material} attachments per document"
                )
    else:
        if len(data.operations) > settings.pr_max_ops_staff:
            raise BadRequestError(
                f"You can include at most {settings.pr_max_ops_staff} changes per contribution"
            )

    # Open PR limit for non-staff users
    if not current_user.is_staff:
        open_count = await db.scalar(
            select(func.count())
            .select_from(PullRequest)
            .where(
                PullRequest.author_id == current_user.id,
                PullRequest.status == PRStatus.OPEN,
            )
        )
        if open_count and open_count >= settings.pr_max_open_per_user:
            raise BadRequestError(
                f"You already have {settings.pr_max_open_per_user} contributions pending review. "
                "Wait for one to be reviewed before submitting another."
            )

    max_bytes = settings.max_file_size_mb * 1024 * 1024

    for op in data.operations:
        ds = getattr(op, "diff_summary", None)
        if ds and len(ds.encode("utf-8")) > max_bytes:
            raise BadRequestError(
                f"The changes in one of your files are too large ({len(ds.encode('utf-8'))} bytes). "
                f"The current limit is {max_bytes // (1024 * 1024)} MB."
            )

    # Validate file_key ownership.
    user_upload_prefix = f"uploads/{current_user.id}/"
    cas_prefix = "cas/"
    keys_to_check: set[str] = set()
    declared_hashes: dict[str, str] = {}
    for op in data.operations:
        file_key = getattr(op, "file_key", None)
        if file_key:
            if not (file_key.startswith(user_upload_prefix) or file_key.startswith(cas_prefix)):
                raise BadRequestError("One of the attached files does not belong to your account")
            keys_to_check.add(file_key)
            content_sha = getattr(op, "content_sha256", None)
            if content_sha:
                declared_hashes[file_key] = str(content_sha)

        if getattr(op, "op", None) == "create_material":
            for att in getattr(op, "attachments", []):
                att_fk = (
                    att.file_key
                    if hasattr(att, "file_key")
                    else (att.get("file_key") if isinstance(att, dict) else None)
                )
                if att_fk:
                    if not (att_fk.startswith(user_upload_prefix) or att_fk.startswith(cas_prefix)):
                        raise BadRequestError(
                            "One of the attachment files does not belong to your account"
                        )
                    keys_to_check.add(att_fk)
                    att_sha = (
                        getattr(att, "content_sha256", None)
                        if not isinstance(att, dict)
                        else att.get("content_sha256")
                    )
                    if att_sha:
                        declared_hashes[att_fk] = str(att_sha)

            pmid = getattr(op, "parent_material_id", None)
            if pmid:
                actual_pmid: uuid.UUID | None = None
                if isinstance(pmid, uuid.UUID):
                    actual_pmid = pmid
                elif isinstance(pmid, str) and not pmid.startswith("$"):
                    with contextlib.suppress(ValueError):
                        actual_pmid = uuid.UUID(pmid)

                if actual_pmid:
                    parent_mat = await db.scalar(select(Material).where(Material.id == actual_pmid))
                    if parent_mat and parent_mat.parent_material_id is not None:
                        raise BadRequestError("Cannot attach a material to another attachment")

    if keys_to_check:
        existence_results = await asyncio.gather(*(object_exists(k) for k in keys_to_check))
        for key, exists in zip(keys_to_check, existence_results, strict=False):
            if not exists:
                raise BadRequestError(
                    "One or more uploaded files could not be found. "
                    "They may have expired — try uploading again."
                )

        # Every key must be backed by either this user's clean Upload row or an
        # unexpired, single-use generated-CAS claim.
        upload_rows = list(
            (
                await db.scalars(
                    select(Upload).where(
                        (Upload.final_key.in_(keys_to_check))
                        | (Upload.quarantine_key.in_(keys_to_check)),
                        Upload.status == "clean",
                        Upload.user_id == current_user.id,
                    )
                )
            ).all()
        )
        authorized_hashes: dict[str, str] = {}
        for upload in upload_rows:
            authorized_key = (
                upload.final_key if upload.final_key in keys_to_check else upload.quarantine_key
            )
            reference_sha = upload.content_sha256 or upload.sha256
            if authorized_key:
                # Empty hash supports pre-migration clean Upload rows while
                # preserving ownership and scan-state authorization.
                authorized_hashes[authorized_key] = reference_sha or ""

        now = datetime.now(UTC)
        claims = list(
            (
                await db.scalars(
                    select(CasStagingClaim)
                    .where(
                        CasStagingClaim.user_id == current_user.id,
                        CasStagingClaim.file_key.in_(keys_to_check),
                        CasStagingClaim.consumed_at.is_(None),
                        CasStagingClaim.expires_at > now,
                    )
                    .with_for_update()
                )
            ).all()
        )
        claims_by_key = {claim.file_key: claim for claim in claims}
        for claim in claims:
            authorized_hashes.setdefault(claim.file_key, claim.sha256)

        for key in keys_to_check:
            expected_sha = authorized_hashes.get(key)
            if expected_sha is None:
                raise BadRequestError(
                    "One or more files do not have a valid security claim for your account."
                )
            if expected_sha and key.startswith(cas_prefix) and declared_hashes.get(key) != expected_sha:
                raise BadRequestError("A CAS file hash does not match its verified upload claim")

        for key, claim in claims_by_key.items():
            if key not in {row.final_key for row in upload_rows}:
                claim.consumed_at = now

    # Serialize operations to list[dict]
    ops_payload = [op.model_dump(mode="json") for op in data.operations]
    summary_types = sorted({op.op for op in data.operations})

    has_file = any(
        op_dict.get("file_key")
        or any(
            isinstance(att, dict) and att.get("file_key")
            for att in (op_dict.get("attachments") or [])
        )
        for op_dict in ops_payload
    )

    pr = PullRequest(
        id=uuid.uuid4(),
        type="batch",
        status=PRStatus.OPEN,
        title=data.title,
        description=data.description,
        payload=ops_payload,
        summary_types=summary_types,
        author_id=current_user.id,
        virus_scan_result=VirusScanResult.CLEAN if has_file else VirusScanResult.SKIPPED,
    )
    db.add(pr)
    await db.flush()

    # Claim file keys atomically via DB unique constraint.
    if keys_to_check:
        for fk in keys_to_check:
            db.add(PRFileClaim(file_key=fk, pr_id=pr.id))
        try:
            await db.flush()
        except IntegrityError:
            raise BadRequestError(
                "One or more files are already included in another pending contribution. "
                "Please wait for that contribution to be reviewed first."
            )

    # Auto-approve for privileged users if their setting is enabled.
    # If any uploaded file is still being post-processed (processing_status not settled),
    # defer the merge: create the PR as OPEN with auto_merge_pending=True.
    # process_upload_post_scan will trigger the merge once all files settle.
    if current_user.is_moderator and current_user.auto_approve:
        cas_keys = [k for k in keys_to_check if k.startswith("cas/")]
        all_settled = True
        if cas_keys:
            unsettled = await db.scalar(
                select(func.count())
                .select_from(Upload)
                .where(
                    Upload.final_key.in_(cas_keys),
                    Upload.processing_status.not_in(["complete", "degraded"]),
                )
            )
            if unsettled:
                all_settled = False

        if all_settled:
            pr.status = PRStatus.APPROVED
            pr.reviewed_by = current_user.id
            await apply_pr(db, pr)
            await _cleanup_pr_resources(db, pr, redis=redis)

            broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
            closed_event = {"type": "pr_closed", "id": str(pr.id)}
            for topic in _pr_directory_topics(list(pr.payload)):
                broadcasts.append((topic, closed_event))
            if pr.author_id is not None:
                broadcasts.append((f"pr_updates:{pr.author_id}", closed_event))
        else:
            # Files still processing — hold the PR open and merge once they settle.
            pr.auto_merge_pending = True

    await db.refresh(pr, ["author", "created_at", "updated_at"])

    if pr.status == PRStatus.OPEN:
        broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
        event = {"type": "pr_opened", "id": str(pr.id)}
        for topic in _pr_directory_topics(list(pr.payload)):
            broadcasts.append((topic, event))
        if pr.author_id is not None:
            broadcasts.append((f"pr_updates:{pr.author_id}", event))

    return pr


async def apply_pr(db: AsyncSession, pr: PullRequest) -> None:
    """
    Execute all operations in a batch PR.  Operations are topologically sorted
    so that temp_id producers run before consumers, then executed sequentially
    within a single DB transaction.

    The original pr.payload is never mutated.  After execution, pr.applied_result
    is set to an enriched copy of the sorted operations, each annotated with:
      - result_id:          UUID string of the created/edited/deleted item
      - result_browse_path: slug-path usable as /browse/<path>
      - pre_state:          snapshot of the entity before mutation (for edit/move ops)
    """
    if pr.applied_result is not None:
        return

    sorted_ops = topo_sort_operations(list(pr.payload))
    id_map: dict[str, uuid.UUID] = {}
    result_ops: list[dict[str, typing.Any]] = []

    for op in sorted_ops:
        op_type = str(op.get("op") or op.get("pr_type") or "")  # pr_type for legacy rows
        if not op_type:
            raise BadRequestError(f"Operation missing 'op' field: {op}")

        executor = _EXECUTORS.get(op_type)
        if not executor:
            raise BadRequestError(f"Unknown operation type: {op_type}")

        is_delete = "delete" in op_type

        # Capture pre-state before mutation (for revertable edits/moves)
        pre_state: dict[str, typing.Any] | None = None
        if op_type in ("edit_material", "edit_directory", "move_item"):
            pre_state = await _capture_pre_state(db, op_type, op, id_map)

        # For deletes, capture the browse path BEFORE the item is removed
        pre_delete_browse_path: str | None = None
        if is_delete:
            try:
                id_field = "directory_id" if "directory" in op_type else "material_id"
                raw_id = str(op.get(id_field, ""))
                target_uuid = _resolve(raw_id, id_map) if raw_id else None
                if target_uuid:
                    pre_delete_browse_path = await _build_browse_path(db, op_type, target_uuid)
            except Exception:
                pass

        result_id = await executor(db, op, pr, id_map)

        # Register temp_id -> real UUID for downstream inter-op references
        temp_id = str(op.get("temp_id")) if op.get("temp_id") else None
        if temp_id:
            id_map[temp_id] = result_id

        # Build the enriched operation record for applied_result
        enriched: dict[str, typing.Any] = dict(op)
        enriched["result_id"] = str(result_id)
        if pre_state is not None:
            enriched["pre_state"] = pre_state
        if is_delete and pre_delete_browse_path:
            enriched["result_browse_path"] = pre_delete_browse_path
        elif not is_delete:
            try:
                browse_path = await _build_browse_path(db, op_type, result_id)
                if browse_path:
                    enriched["result_browse_path"] = browse_path
            except Exception:
                pass

        result_ops.append(enriched)

    pr.applied_result = result_ops
    flag_modified(pr, "applied_result")
    pr.approved_at = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Revert executors (Phase 5)
# ---------------------------------------------------------------------------


async def _exec_undelete_material(
    db: AsyncSession, p: dict[str, typing.Any], pr: PullRequest, id_map: dict[str, uuid.UUID]
) -> uuid.UUID:
    """Restore a soft-deleted material and all its attachments."""
    mat_id = _resolve(str(p["material_id"]), id_map)
    mat = await db.scalar(
        select(Material).where(Material.id == mat_id).execution_options(include_deleted=True)
    )
    if not mat:
        raise NotFoundError("Material not found (even among deleted)")

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
    """Restore a soft-deleted directory and its entire subtree."""
    dir_id = _resolve(str(p["directory_id"]), id_map)

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
    all_dir_ids = (
        await db.scalars(select(dir_cte.c.id).execution_options(include_deleted=True))
    ).all()

    for did in all_dir_ids:
        d = await db.scalar(
            select(Directory).where(Directory.id == did).execution_options(include_deleted=True)
        )
        if d:
            d.deleted_at = None

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

    if dir_id is None:
        raise BadRequestError("Directory ID is required")
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
    dir_id = _resolve(str(p["directory_id"]), id_map)
    pre = p.get("pre_state", {})
    d = await db.scalar(
        select(Directory).where(Directory.id == dir_id).options(selectinload(Directory.tags))
    )
    if not d:
        raise NotFoundError("Directory not found")

    name_changed = d.name != pre["name"]
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
    pre = p.get("pre_state", {})

    if pre.get("target_type") == "directory":
        d = await db.scalar(select(Directory).where(Directory.id == target_id))
        if not d:
            raise NotFoundError("Directory not found")
        prev_parent = uuid.UUID(pre["prev_parent_id"]) if pre.get("prev_parent_id") else None
        d.parent_id = prev_parent
        await db.flush()
        await _enqueue_reindex_directory_recursive(db, d.id)
        return d.id
    else:
        mat = await db.scalar(select(Material).where(Material.id == target_id))
        if not mat:
            raise NotFoundError("Material not found")
        prev_dir = uuid.UUID(pre["prev_directory_id"]) if pre.get("prev_directory_id") else None
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

    for op in reverse_ops:
        op_type = str(op["op"])
        executor = _REVERT_DISPATCH.get(op_type)
        if not executor:
            raise BadRequestError(f"No revert executor for op type: {op_type}")

        result_id = await executor(db, op, revert, id_map)

        enriched_rev: dict[str, typing.Any] = dict(op)
        enriched_rev["result_id"] = str(result_id)
        try:
            bp = await _build_browse_path(db, op_type, result_id)
            if bp:
                enriched_rev["result_browse_path"] = bp
        except Exception:
            pass
        result_ops.append(enriched_rev)

    revert.applied_result = result_ops
    flag_modified(revert, "applied_result")

    original_pr.reverted_by_pr_id = revert.id

    return revert


# ---------------------------------------------------------------------------
# High-level Service Functions
# ---------------------------------------------------------------------------


async def list_prs_service(
    db: AsyncSession,
    status: str | None = None,
    type: str | None = None,
    author_id: uuid.UUID | None = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[PullRequest], int]:
    """List pull requests with filtering and pagination. Returns (prs, total_count)."""
    base_stmt = select(PullRequest)
    if status:
        base_stmt = base_stmt.where(PullRequest.status == status)
    if type:
        base_stmt = base_stmt.where(PullRequest.type == type)
    if author_id:
        base_stmt = base_stmt.where(PullRequest.author_id == author_id)

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total_count = await db.scalar(count_stmt) or 0

    stmt = (
        base_stmt.options(selectinload(PullRequest.author))
        .order_by(PullRequest.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    result = await db.execute(stmt)
    prs = list(result.scalars().all())

    return prs, total_count


async def list_prs_for_item_service(
    db: AsyncSession,
    target_type: str,
    target_id: str,
    page: int = 1,
    limit: int = 10,
) -> tuple[list[PullRequest], int]:
    """Search for open PRs referencing a specific material or directory."""
    from sqlalchemy import text

    base_stmt = (
        select(PullRequest)
        .options(selectinload(PullRequest.author))
        .where(PullRequest.status == PRStatus.OPEN)
    )

    if db.bind.dialect.name == "sqlite":
        # SQLite fallback: fetch all open PRs and filter in Python
        result = await db.execute(base_stmt.order_by(PullRequest.created_at.desc()))
        prs = list(result.scalars().all())
        filtered = []
        for pr in prs:
            match = False
            for op in pr.payload:
                if target_type == "material":
                    if (
                        op.get("material_id") == target_id
                        or op.get("parent_material_id") == target_id
                    ):
                        match = True
                        break
                elif target_type == "directory":
                    if target_id == "root":
                        if (
                            (op.get("op") == "create_material" and op.get("directory_id") is None)
                            or (op.get("op") == "create_directory" and op.get("parent_id") is None)
                            or (op.get("op") == "move_item" and op.get("new_parent_id") is None)
                        ):
                            match = True
                            break
                    else:
                        if (
                            op.get("directory_id") == target_id
                            or op.get("parent_id") == target_id
                            or op.get("new_parent_id") == target_id
                        ):
                            match = True
                            break
            if match:
                filtered.append(pr)
        total_count = len(filtered)
        prs = filtered[(page - 1) * limit : page * limit]
    else:
        if target_type == "material":
            stmt = base_stmt.where(
                text(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements(payload) elem "
                    "WHERE elem->>'material_id' = :tid "
                    "OR elem->>'parent_material_id' = :tid)"
                ).bindparams(tid=target_id)
            )
        elif target_type == "directory":
            if target_id == "root":
                stmt = base_stmt.where(
                    text(
                        "EXISTS (SELECT 1 FROM jsonb_array_elements(payload) elem "
                        "WHERE (elem->>'op' = 'create_material' AND elem->>'directory_id' IS NULL) "
                        "OR (elem->>'op' = 'create_directory' AND elem->>'parent_id' IS NULL) "
                        "OR (elem->>'op' = 'move_item' AND elem->>'new_parent_id' IS NULL))"
                    )
                )
            else:
                stmt = base_stmt.where(
                    text(
                        "EXISTS (SELECT 1 FROM jsonb_array_elements(payload) elem "
                        "WHERE elem->>'directory_id' = :tid "
                        "OR elem->>'parent_id' = :tid "
                        "OR elem->>'new_parent_id' = :tid)"
                    ).bindparams(tid=target_id)
                )
        else:
            raise BadRequestError("Invalid targetType")

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = await db.scalar(count_stmt) or 0

        paginated_stmt = (
            stmt.order_by(PullRequest.created_at.desc()).offset((page - 1) * limit).limit(limit)
        )
        result = await db.execute(paginated_stmt)
        prs = list(result.scalars().all())

    return prs, total_count


async def approve_pr_service(db: AsyncSession, pr_id: uuid.UUID, reviewer: User) -> PullRequest:
    """Approve and apply a contribution."""
    pr = await db.scalar(select(PullRequest).where(PullRequest.id == pr_id))
    if not pr:
        raise NotFoundError("Pull request not found")

    if pr.status != PRStatus.OPEN:
        raise BadRequestError("This contribution is no longer open")

    pr.status = PRStatus.APPROVED
    pr.reviewed_by = reviewer.id

    await apply_pr(db, pr)
    await _cleanup_pr_resources(db, pr)

    broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
    event = {"type": "pr_closed", "id": str(pr.id)}
    for topic in _pr_directory_topics(list(pr.payload)):
        broadcasts.append((topic, event))
    if pr.author_id is not None:
        broadcasts.append((f"pr_updates:{pr.author_id}", event))

    if pr.author_id is not None:
        await notify_user(
            db,
            pr.author_id,
            "pr_approved",
            f'Your contribution "{pr.title}" was published',
            link=f"/pull-requests/{pr.id}",
        )
    return pr


async def reject_pr_service(
    db: AsyncSession, pr_id: uuid.UUID, reason: str, reviewer: User
) -> PullRequest:
    """Reject a contribution and clean up its staging files."""
    pr = await db.scalar(select(PullRequest).where(PullRequest.id == pr_id))
    if not pr:
        raise NotFoundError("Pull request not found")

    if pr.status != PRStatus.OPEN:
        raise BadRequestError("This contribution is no longer open")

    pr.status = PRStatus.REJECTED
    pr.reviewed_by = reviewer.id
    pr.rejection_reason = reason

    await _cleanup_pr_resources(db, pr, delete_staging=True)

    broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
    event = {"type": "pr_closed", "id": str(pr.id)}
    for topic in _pr_directory_topics(list(pr.payload)):
        broadcasts.append((topic, event))
    if pr.author_id is not None:
        broadcasts.append((f"pr_updates:{pr.author_id}", event))

    if pr.author_id is not None:
        await notify_user(
            db,
            pr.author_id,
            "pr_rejected",
            f'Your contribution "{pr.title}" was not accepted',
            link=f"/pull-requests/{pr.id}",
        )
    return pr


async def cancel_pr_service(db: AsyncSession, pr_id: uuid.UUID, current_user: User) -> PullRequest:
    """Author cancels their own open pull request."""
    pr = await db.scalar(select(PullRequest).where(PullRequest.id == pr_id))
    if not pr:
        raise NotFoundError("Pull request not found")

    if pr.author_id != current_user.id:
        raise ForbiddenError("Only the author can cancel this contribution")

    if pr.status != PRStatus.OPEN:
        raise BadRequestError("This contribution is no longer open")

    pr.status = PRStatus.CANCELLED
    await _cleanup_pr_resources(db, pr, delete_staging=True)

    broadcasts = db.info.setdefault(PostCommitKey.SSE, [])
    event = {"type": "pr_closed", "id": str(pr.id)}
    for topic in _pr_directory_topics(list(pr.payload)):
        broadcasts.append((topic, event))
    if pr.author_id is not None:
        broadcasts.append((f"pr_updates:{pr.author_id}", event))

    return pr


async def revert_pr_service(db: AsyncSession, pr_id: uuid.UUID, admin: User) -> PullRequest:
    """Validate and execute a revert for an approved PR."""
    pr = await db.scalar(
        select(PullRequest).where(PullRequest.id == pr_id).options(selectinload(PullRequest.author))
    )
    if not pr:
        raise NotFoundError("Pull request not found")

    if pr.status != PRStatus.APPROVED:
        raise BadRequestError("Only approved contributions can be reverted")

    if pr.type == "revert":
        raise BadRequestError("Revert contributions cannot themselves be reverted")

    if pr.reverted_by_pr_id is not None:
        raise BadRequestError("This contribution has already been reverted")

    if not pr.is_revertable:
        raise BadRequestError("The 7-day revert grace period has expired")

    revert = await revert_pr(db, pr, admin.id)
    await db.refresh(revert, ["author", "created_at", "updated_at"])

    if pr.author_id and pr.author_id != admin.id:
        await notify_user(
            db,
            pr.author_id,
            "pr_reverted",
            f'Your contribution "{pr.title}" has been reverted',
            link=f"/pull-requests/{revert.id}",
        )

    return revert


async def get_pr_preview_service(
    db: AsyncSession, pr_id: uuid.UUID, op_index: int, current_user: User
) -> dict[str, typing.Any]:
    """Resolve a presigned URL for a file in a PR operation."""
    from app.core.storage.facade import generate_presigned_get

    pr = await db.scalar(select(PullRequest).where(PullRequest.id == pr_id))
    if not pr:
        raise NotFoundError("Pull request not found")

    # SECURITY (S13): Restrict preview access to author and moderators
    if not pr.can_be_managed_by(current_user):
        raise ForbiddenError("You are not authorized to preview this pull request")

    if op_index >= len(pr.payload):
        raise BadRequestError("Operation index out of range")

    op = pr.payload[op_index]

    file_key = op.get("file_key")
    file_name = op.get("file_name")
    file_mime_type = op.get("file_mime_type")

    # Handle move_item preview resolution
    if not file_key and op.get("op") == "move_item" and op.get("target_type") == "material":
        target_id_raw = op.get("target_id")
        if target_id_raw:
            target_id_str = str(target_id_raw)
            if target_id_str.startswith("$"):
                # Reference to a temp_id in the same PR
                source_op = next((o for o in pr.payload if o.get("temp_id") == target_id_str), None)
                if source_op:
                    file_key = typing.cast(str | None, source_op.get("file_key"))
                    file_name = typing.cast(str | None, source_op.get("file_name"))
                    file_mime_type = typing.cast(str | None, source_op.get("file_mime_type"))
            else:
                # Reference to a real material UUID
                try:
                    target_uuid = uuid.UUID(target_id_str)
                    mv = await db.scalar(
                        select(MaterialVersion)
                        .where(MaterialVersion.material_id == target_uuid)
                        .order_by(MaterialVersion.version_number.desc())
                        .limit(1)
                    )
                    if mv:
                        file_key = mv.file_key
                        file_name = mv.file_name
                        file_mime_type = mv.file_mime_type
                except (ValueError, TypeError):
                    pass

    if not file_key:
        raise NotFoundError("No file to preview for this operation")

    file_key_str = str(file_key)
    file_name_str = str(file_name) if file_name else None
    file_mime_type_str = str(file_mime_type) if file_mime_type else None

    # Legacy V1: after approval, files were moved from uploads/ to materials/
    if pr.status == "approved" and file_key_str.startswith("uploads/"):
        file_key_str = file_key_str.replace("uploads/", "materials/", 1)

    # Refuse to serve unscanned quarantine files
    if file_key_str.startswith("quarantine/"):
        raise BadRequestError("File is still being processed and cannot be previewed yet.")

    url = await generate_presigned_get(
        file_key_str,
        filename=file_name_str,
        content_type=file_mime_type_str,
    )
    return {
        "url": url,
        "file_name": file_name_str,
        "file_mime_type": file_mime_type_str,
    }


async def get_pr_diff_service(db: AsyncSession, pr_id: uuid.UUID) -> dict[str, typing.Any]:
    """Calculate a summary of file changes in a PR."""
    pr = await db.scalar(select(PullRequest).where(PullRequest.id == pr_id))
    if not pr:
        raise NotFoundError("Pull request not found")

    file_ops = [op for op in pr.payload if op.get("file_key")]
    if not file_ops:
        return {"diff": None}

    return {"diff": f"{len(file_ops)} file(s) changed."}
