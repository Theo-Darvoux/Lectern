import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.annotation import Annotation
from app.models.base import UUIDMixin
from app.models.comment import Comment
from app.models.flag import Flag, FlagStatus
from app.models.material import Material
from app.models.pull_request import PRComment, PullRequest
from app.models.user import User
from app.services.directory import get_directory_paths
from app.services.notification import MODERATOR_ROLES

TARGET_TABLE_MAP: dict[str, type[UUIDMixin]] = {
    "material": Material,
    "annotation": Annotation,
    "pull_request": PullRequest,
    "comment": Comment,
    "pr_comment": PRComment,
}


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except ValueError:
        raise BadRequestError(f"Invalid UUID: {value}")


async def _validate_target(db: AsyncSession, target_type: str, target_id: uuid.UUID) -> None:
    model = TARGET_TABLE_MAP.get(target_type)
    if not model:
        raise BadRequestError(f"Invalid target_type: {target_type}")
    target_res = await db.execute(select(model).where(model.id == target_id))
    if not target_res.scalar_one_or_none():
        raise NotFoundError(f"{target_type} not found")


async def create_flag(
    db: AsyncSession,
    reporter_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    reason: str,
    description: str | None = None,
) -> Flag:
    await _validate_target(db, target_type, target_id)

    existing = await db.execute(
        select(Flag).where(
            Flag.reporter_id == reporter_id,
            Flag.target_type == target_type,
            Flag.target_id == target_id,
        )
    )
    if existing.scalar_one_or_none():
        raise BadRequestError("You have already flagged this item")

    flag = Flag(
        reporter_id=reporter_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        description=description,
    )
    db.add(flag)
    await db.flush()

    result = await db.execute(
        select(Flag).options(selectinload(Flag.reporter)).where(Flag.id == flag.id)
    )
    return result.scalar_one()


async def list_flags(
    db: AsyncSession,
    limit: int,
    offset: int,
    status: str | None = None,
    target_type: str | None = None,
) -> tuple[list[Flag], int]:
    base = select(Flag)
    if status:
        base = base.where(Flag.status == FlagStatus(status))
    if target_type:
        base = base.where(Flag.target_type == target_type)

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        base.options(selectinload(Flag.reporter), selectinload(Flag.resolver))
        .order_by(Flag.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().unique().all()), total


async def update_flag(
    db: AsyncSession,
    flag_id: str,
    user: User,
    status: str,
) -> Flag:
    if user.role not in MODERATOR_ROLES:
        raise ForbiddenError("Only moderators can update flags")

    fid = _to_uuid(flag_id)
    result = await db.execute(
        select(Flag)
        .options(selectinload(Flag.reporter), selectinload(Flag.resolver))
        .where(Flag.id == fid)
    )
    flag = result.scalar_one_or_none()
    if not flag:
        raise NotFoundError("Flag not found")

    flag.status = FlagStatus(status)
    flag.resolved_by = user.id
    flag.resolved_at = datetime.now(UTC)
    await db.flush()
    return flag


@dataclass
class TargetInfo:
    """Resolved preview + deep link for a flagged item (see schemas.flag.FlagTarget)."""

    exists: bool
    preview: str | None = None
    link: str | None = None


def _truncate(text: str | None, limit: int = 140) -> str | None:
    if not text:
        return None
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def resolve_flag_targets(
    db: AsyncSession, flags: Sequence[Flag]
) -> dict[uuid.UUID, TargetInfo]:
    """Resolve each flag's target into a content preview and a navigable parent
    link, batching DB access by target type to avoid per-flag queries.

    A missing row yields ``exists=False`` (the reported content was deleted).
    """
    by_type: dict[str, set[uuid.UUID]] = defaultdict(set)
    for f in flags:
        by_type[f.target_type].add(f.target_id)

    async def _fetch[M: UUIDMixin](model: type[M], ids: set[uuid.UUID]) -> dict[uuid.UUID, M]:
        if not ids:
            return {}
        rows = await db.execute(select(model).where(model.id.in_(ids)))
        return {row.id: row for row in rows.scalars()}

    materials = await _fetch(Material, by_type.get("material", set()))
    annotations = await _fetch(Annotation, by_type.get("annotation", set()))
    comments = await _fetch(Comment, by_type.get("comment", set()))
    prs = await _fetch(PullRequest, by_type.get("pull_request", set()))
    pr_comments = await _fetch(PRComment, by_type.get("pr_comment", set()))

    # Materials whose browse path we need: directly-flagged materials, the parent
    # material of each flagged annotation, and material-scoped flagged comments.
    path_materials: dict[uuid.UUID, Material] = dict(materials)
    needed_material_ids: set[uuid.UUID] = set()
    for ann in annotations.values():
        needed_material_ids.add(ann.material_id)
    comment_dir_ids: set[uuid.UUID] = set()
    for com in comments.values():
        if com.target_type == "material":
            needed_material_ids.add(com.target_id)
        elif com.target_type == "directory":
            comment_dir_ids.add(com.target_id)

    extra_ids = needed_material_ids - path_materials.keys()
    if extra_ids:
        path_materials.update(await _fetch(Material, extra_ids))

    dir_ids = {m.directory_id for m in path_materials.values() if m.directory_id} | comment_dir_ids
    dir_paths = await get_directory_paths(db, dir_ids)

    def material_link(m: Material) -> str:
        dpath = dir_paths.get(m.directory_id) if m.directory_id else None
        return f"/browse/{dpath}/{m.slug}" if dpath else f"/browse/{m.slug}"

    result: dict[uuid.UUID, TargetInfo] = {}
    for f in flags:
        tid = f.target_id
        if f.target_type == "material":
            m = materials.get(tid)
            result[f.id] = (
                TargetInfo(True, _truncate(m.title), material_link(m)) if m else TargetInfo(False)
            )
        elif f.target_type == "annotation":
            a = annotations.get(tid)
            if a is not None:
                parent = path_materials.get(a.material_id)
                link = material_link(parent) if parent is not None else None
                result[f.id] = TargetInfo(True, _truncate(a.body), link)
            else:
                result[f.id] = TargetInfo(False)
        elif f.target_type == "comment":
            c = comments.get(tid)
            if c is not None:
                if c.target_type == "material":
                    parent = path_materials.get(c.target_id)
                    link = material_link(parent) if parent is not None else None
                else:  # directory-scoped comment
                    dpath = dir_paths.get(c.target_id)
                    link = f"/browse/{dpath}" if dpath else "/browse"
                result[f.id] = TargetInfo(True, _truncate(c.body), link)
            else:
                result[f.id] = TargetInfo(False)
        elif f.target_type == "pull_request":
            p = prs.get(tid)
            result[f.id] = (
                TargetInfo(True, _truncate(p.title), f"/pull-requests/{p.id}")
                if p
                else TargetInfo(False)
            )
        elif f.target_type == "pr_comment":
            pc = pr_comments.get(tid)
            result[f.id] = (
                TargetInfo(True, _truncate(pc.body), f"/pull-requests/{pc.pr_id}")
                if pc
                else TargetInfo(False)
            )
        else:
            result[f.id] = TargetInfo(False)
    return result
