from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.core.database.database import get_db
from app.core.database.post_commit import add_post_commit_job
from app.dependencies.auth import require_role
from app.models.content_status import ContentStatus
from app.models.dead_letter import DeadLetterJob
from app.models.flag import Flag, FlagStatus
from app.models.material import Material
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.services.auth import ensure_admin_removal_safe, lock_admin_authority_change
from app.services.directory import get_directory_paths
from app.services.notification import notify_user
from app.services.user import hard_delete_user

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_ROLES = (UserRole.BUREAU, UserRole.VIEUX)
AdminUser = Annotated[User, Depends(require_role(UserRole.BUREAU, UserRole.VIEUX))]


class BulkUserActionIn(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    action: Literal["approve", "reject", "set_role", "delete"]
    role: UserRole | None = None


class ContentStatusUpdateIn(BaseModel):
    material_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    status: ContentStatus


def _dedupe_ids(values: list[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(values))


def _browse_path(material: Material, directory_paths: dict[uuid.UUID, str]) -> str:
    if material.directory_id:
        parent = directory_paths.get(material.directory_id)
        if parent:
            return f"/browse/{parent}/{material.slug}"
    return f"/browse/{material.slug}"


@router.get("/overview")
async def admin_operational_overview(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    pending_users = (
        await db.scalar(select(func.count()).select_from(User).where(User.role == UserRole.PENDING))
        or 0
    )
    open_prs = (
        await db.scalar(
            select(func.count()).select_from(PullRequest).where(PullRequest.status == PRStatus.OPEN)
        )
        or 0
    )
    unresolved_flags = (
        await db.scalar(
            select(func.count())
            .select_from(Flag)
            .where(Flag.status.in_([FlagStatus.OPEN, FlagStatus.REVIEWING]))
        )
        or 0
    )
    failed_jobs = (
        await db.scalar(
            select(func.count())
            .select_from(DeadLetterJob)
            .where(DeadLetterJob.resolved_at.is_(None))
        )
        or 0
    )

    status_rows = await db.execute(
        select(Material.status, func.count())
        .where(Material.deleted_at.is_(None))
        .group_by(Material.status)
    )
    content_counts = {status.value: 0 for status in ContentStatus}
    for status, count in status_rows.all():
        if status in content_counts:
            content_counts[status] = int(count)

    recent_pending = list(
        (
            await db.scalars(
                select(User)
                .where(User.role == UserRole.PENDING)
                .order_by(User.created_at.desc())
                .limit(5)
            )
        ).all()
    )
    recent_prs = list(
        (
            await db.scalars(
                select(PullRequest)
                .where(PullRequest.status == PRStatus.OPEN)
                .order_by(PullRequest.updated_at.desc())
                .limit(5)
            )
        ).all()
    )
    recent_flags = list(
        (
            await db.scalars(
                select(Flag)
                .where(Flag.status.in_([FlagStatus.OPEN, FlagStatus.REVIEWING]))
                .order_by(Flag.created_at.desc())
                .limit(5)
            )
        ).all()
    )

    return {
        "attention": {
            "pending_users": pending_users,
            "open_pull_requests": open_prs,
            "moderation_flags": unresolved_flags,
            "failed_jobs": failed_jobs,
        },
        "content": {
            "total": sum(content_counts.values()),
            **content_counts,
        },
        "recent": {
            "pending_users": [
                {
                    "id": str(user.id),
                    "email": user.email,
                    "display_name": user.display_name,
                    "created_at": user.created_at.isoformat(),
                }
                for user in recent_pending
            ],
            "open_pull_requests": [
                {
                    "id": str(pr.id),
                    "title": pr.title,
                    "created_at": pr.created_at.isoformat(),
                    "updated_at": pr.updated_at.isoformat(),
                }
                for pr in recent_prs
            ],
            "moderation_flags": [
                {
                    "id": str(flag.id),
                    "reason": flag.reason,
                    "target_type": flag.target_type,
                    "created_at": flag.created_at.isoformat(),
                }
                for flag in recent_flags
            ],
        },
    }


@router.post("/users/bulk")
async def admin_bulk_users(
    body: Annotated[BulkUserActionIn, Body()],
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    if body.action == "set_role":
        if body.role is None:
            raise BadRequestError("role is required for set_role")
        if body.role == UserRole.PENDING:
            raise BadRequestError("PENDING can only be entered through account approval")

    updated: list[str] = []
    skipped: list[dict[str, str]] = []

    for user_id in _dedupe_ids(body.user_ids):
        if user_id == _user.id:
            skipped.append({"id": str(user_id), "reason": "Cannot bulk-change your own account"})
            continue

        _, target = await lock_admin_authority_change(
            db,
            _user.id,
            user_id,
            expected_auth_generation=_user.auth_generation,
        )
        if target is None:
            skipped.append({"id": str(user_id), "reason": "User not found"})
            continue

        if body.action == "approve":
            if target.role != UserRole.PENDING:
                skipped.append({"id": str(user_id), "reason": "User is not pending"})
                continue
            target.role = UserRole.STUDENT
            target.auth_generation += 1
            await db.flush()
            await notify_user(
                db,
                target.id,
                notification_type="access_approved",
                title="Access approved",
                body="Your account has been approved. Welcome!",
                link="/",
            )
        elif body.action == "reject":
            if target.role != UserRole.PENDING:
                skipped.append({"id": str(user_id), "reason": "User is not pending"})
                continue
            await hard_delete_user(db, target)
        elif body.action == "set_role":
            assert body.role is not None
            if target.is_admin and body.role not in ADMIN_ROLES:
                await ensure_admin_removal_safe(db, target.id)
            if target.role != body.role:
                target.role = body.role
                target.auth_generation += 1
                await db.flush()
        elif body.action == "delete":
            if target.is_admin:
                await ensure_admin_removal_safe(db, target.id)
            await hard_delete_user(db, target)

        updated.append(str(user_id))

    return {
        "action": body.action,
        "updated": updated,
        "updated_count": len(updated),
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


@router.get("/content")
async def admin_list_content(
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, object]:
    base = select(Material).where(Material.deleted_at.is_(None))

    if status:
        try:
            status_value = ContentStatus(status).value
        except ValueError as exc:
            raise BadRequestError(f"Invalid content status: {status}") from exc
        base = base.where(Material.status == status_value)

    if search:
        pattern = f"%{search.strip()}%"
        base = base.where(or_(Material.title.ilike(pattern), Material.description.ilike(pattern)))

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    materials = list(
        (
            await db.scalars(
                base.order_by(Material.updated_at.desc(), Material.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        ).all()
    )
    directory_ids = {m.directory_id for m in materials if m.directory_id is not None}
    paths = await get_directory_paths(db, directory_ids)

    return {
        "items": [
            {
                "id": str(material.id),
                "title": material.title,
                "type": material.type,
                "status": material.status,
                "updated_at": material.updated_at.isoformat(),
                "total_views": material.total_views,
                "like_count": material.like_count,
                "browse_path": _browse_path(material, paths),
            }
            for material in materials
        ],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.patch("/content/status")
async def admin_update_content_status(
    body: Annotated[ContentStatusUpdateIn, Body()],
    _user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    requested_ids = _dedupe_ids(body.material_ids)
    materials = list(
        (
            await db.scalars(
                select(Material)
                .where(Material.id.in_(requested_ids), Material.deleted_at.is_(None))
                .with_for_update()
            )
        ).all()
    )
    found_ids = {material.id for material in materials}

    for material in materials:
        material.status = body.status.value
    await db.flush()

    if materials:
        add_post_commit_job(
            db,
            ("index_materials_batch", [str(material.id) for material in materials]),
        )

    return {
        "status": body.status.value,
        "updated": [str(material.id) for material in materials],
        "updated_count": len(materials),
        "missing": [
            str(material_id) for material_id in requested_ids if material_id not in found_ids
        ],
    }
