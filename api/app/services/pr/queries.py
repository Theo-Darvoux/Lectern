"""Read-only pull-request queries: listings, file preview and diff summary."""

import re
import typing
import uuid

from sqlalchemy import ColumnElement, String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.common.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.material import MaterialVersion
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User
from app.schemas.pull_request import PROperationFilter, PRSort
from app.services.pr.operations import _is_temp_id
from app.services.pr_visibility import anonymous_material_key, can_see_anonymous_authors

_DIRECTORY_OPS = ("create_directory", "edit_directory", "delete_directory")
_ID_PREFIX = re.compile(r"[0-9a-f-]{4,36}")


def _has_summary_type(op: str) -> ColumnElement[bool]:
    # `summary_types` is a JSON array of fixed op names; matching its text form
    # works on PostgreSQL (JSONB) and SQLite (tests) alike.
    return cast(PullRequest.summary_types, String).like(f'%"{op}"%')


def _op_type_clause(op_type: PROperationFilter) -> ColumnElement[bool]:
    if op_type == "revert":
        return or_(
            PullRequest.type == "revert",
            PullRequest.reverts_pr_id.is_not(None),
            PullRequest.reverted_by_pr_id.is_not(None),
        )
    if op_type == "directory":
        return or_(*(_has_summary_type(op) for op in _DIRECTORY_OPS))
    return _has_summary_type(op_type)


def _search_clause(search: str, viewer: User | None) -> ColumnElement[bool]:
    pattern = f"%{search}%"
    author_name: ColumnElement[bool] = User.display_name.ilike(pattern)
    if not can_see_anonymous_authors(viewer):
        # Searching by name must not reveal who wrote an anonymous PR.
        visible: ColumnElement[bool] = PullRequest.is_anonymous.is_(False)
        if viewer is not None:
            visible = or_(visible, PullRequest.author_id == viewer.id)
        author_name = and_(author_name, visible)
    clauses = [PullRequest.title.ilike(pattern), author_name]
    id_prefix = search.lower().lstrip("#")
    if _ID_PREFIX.fullmatch(id_prefix):
        clauses.append(cast(PullRequest.id, String).ilike(f"{id_prefix}%"))
    return or_(*clauses)


async def list_prs_service(
    db: AsyncSession,
    status: str | None = None,
    type: str | None = None,
    author_id: uuid.UUID | None = None,
    page: int = 1,
    limit: int = 50,
    viewer: User | None = None,
    search: str | None = None,
    op_type: PROperationFilter | None = None,
    sort: PRSort = "newest",
) -> tuple[list[PullRequest], int]:
    """List pull requests with filtering and pagination. Returns (prs, total_count)."""
    base_stmt = select(PullRequest)
    if search and search.strip():
        base_stmt = base_stmt.outerjoin(User, PullRequest.author_id == User.id).where(
            _search_clause(search.strip(), viewer)
        )
    if op_type:
        base_stmt = base_stmt.where(_op_type_clause(op_type))
    if status:
        base_stmt = base_stmt.where(PullRequest.status == status)
    if type:
        base_stmt = base_stmt.where(PullRequest.type == type)
    if author_id:
        base_stmt = base_stmt.where(PullRequest.author_id == author_id)
        # Filtering by author must not attribute anonymous PRs to someone else.
        if not can_see_anonymous_authors(viewer) and (viewer is None or viewer.id != author_id):
            base_stmt = base_stmt.where(PullRequest.is_anonymous.is_(False))

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total_count = await db.scalar(count_stmt) or 0

    if sort == "oldest":
        order_by = [PullRequest.created_at.asc()]
    elif sort == "changes":
        array_length = (
            func.jsonb_array_length
            if db.get_bind().dialect.name == "postgresql"
            else func.json_array_length
        )
        order_by = [array_length(PullRequest.payload).desc(), PullRequest.created_at.desc()]
    else:
        order_by = [PullRequest.created_at.desc()]

    stmt = (
        base_stmt.options(selectinload(PullRequest.author))
        .order_by(*order_by)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    result = await db.execute(stmt)
    prs = list(result.scalars().all())

    return prs, total_count


async def count_prs_service(db: AsyncSession, viewer: User) -> dict[str, int]:
    """Count pull requests per status, and those the viewer authored."""
    rows = await db.execute(select(PullRequest.status, func.count()).group_by(PullRequest.status))
    counts = {str(status): int(total) for status, total in rows.all()}
    mine = await db.scalar(
        select(func.count()).select_from(PullRequest).where(PullRequest.author_id == viewer.id)
    )
    return {
        **{status.value: counts.get(status.value, 0) for status in PRStatus},
        "mine": mine or 0,
    }


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


async def _referenced_material_file(
    db: AsyncSession, pr: PullRequest, material_ref: str
) -> tuple[str | None, str | None, str | None]:
    """Return ``(file_key, file_name, mime_type)`` of a material a PR operation targets.

    The reference is either a temp id created earlier in the same PR or a real id,
    whose latest version is used.
    """
    if _is_temp_id(material_ref):
        source_op = next((o for o in pr.payload if o.get("temp_id") == material_ref), None)
        if source_op is None:
            return None, None, None
        return (
            typing.cast(str | None, source_op.get("file_key")),
            typing.cast(str | None, source_op.get("file_name")),
            typing.cast(str | None, source_op.get("file_mime_type")),
        )
    try:
        material_id = uuid.UUID(material_ref)
    except ValueError:
        return None, None, None
    mv = await db.scalar(
        select(MaterialVersion)
        .where(MaterialVersion.material_id == material_id)
        .order_by(MaterialVersion.version_number.desc())
        .limit(1)
    )
    if mv is None:
        return None, None, None
    return mv.file_key, mv.file_name, mv.file_mime_type


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

    # Operations on an existing material carry no file of their own.
    material_ref = None
    if op.get("op") == "move_item" and op.get("target_type") == "material":
        material_ref = op.get("target_id")
    elif op.get("op") in ("edit_material", "delete_material"):
        material_ref = op.get("material_id")
    if not file_key and material_ref:
        file_key, file_name, file_mime_type = await _referenced_material_file(
            db, pr, str(material_ref)
        )

    if not file_key:
        raise NotFoundError("No file to preview for this operation")

    file_key_str = str(file_key)
    file_name_str = str(file_name) if file_name else None
    file_mime_type_str = str(file_mime_type) if file_mime_type else None

    # Legacy V1: after approval, files were moved from uploads/ to materials/
    if pr.status == "approved" and file_key_str.startswith("uploads/"):
        file_key_str = (
            anonymous_material_key(file_key_str)
            if pr.is_anonymous
            else file_key_str.replace("uploads/", "materials/", 1)
        )

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
