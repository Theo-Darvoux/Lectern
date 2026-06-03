import asyncio
import logging
import typing
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.avatar_processor import process_avatar
from app.core.exceptions import BadRequestError
from app.core.storage import delete_object, download_file, upload_file
from app.models.annotation import Annotation
from app.models.comment import Comment
from app.models.flag import Flag
from app.models.material import Material
from app.models.notification import Notification
from app.models.pull_request import PRComment, PullRequest
from app.models.upload import Upload
from app.models.user import User
from app.models.view_history import ViewHistory
from app.services.material import material_orm_to_dict

logger = logging.getLogger(__name__)


async def onboard_user(
    db: AsyncSession, user: User, display_name: str, academic_year: str, gdpr_consent: bool
) -> User:
    if user.onboarded:
        raise BadRequestError("User already onboarded")
    if not gdpr_consent:
        raise BadRequestError("GDPR consent is required")

    user.display_name = display_name
    user.academic_year = academic_year
    user.gdpr_consent = True
    user.gdpr_consent_at = datetime.now(UTC)
    user.onboarded = True
    await db.flush()
    return user


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    uid = uuid.UUID(str(user_id))
    result = await db.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()


async def get_user_stats(db: AsyncSession, user_id: str) -> dict[str, int]:
    uid = uuid.UUID(str(user_id))
    from app.models.pull_request import PRStatus

    pr_approved = await db.scalar(
        select(func.count()).where(
            PullRequest.author_id == uid, PullRequest.status == PRStatus.APPROVED
        )
    )
    prs_total = await db.scalar(select(func.count()).where(PullRequest.author_id == uid))
    annotations_count = await db.scalar(select(func.count()).where(Annotation.author_id == uid))
    comments_count = await db.scalar(select(func.count()).where(Comment.author_id == uid))
    open_pr_count = await db.scalar(
        select(func.count()).where(
            PullRequest.author_id == uid, PullRequest.status == PRStatus.OPEN
        )
    )

    pr_approved = pr_approved or 0
    annotations_count = annotations_count or 0

    return {
        "prs_approved": pr_approved,
        "prs_total": prs_total or 0,
        "annotations_count": annotations_count,
        "comments_count": comments_count or 0,
        "open_pr_count": open_pr_count or 0,
        "reputation": pr_approved * 10 + annotations_count * 2,
    }


async def get_recently_viewed(
    db: AsyncSession, user_id: str, limit: int = 10
) -> list[dict[str, typing.Any]]:
    uid = uuid.UUID(str(user_id))
    from sqlalchemy.orm import selectinload

    from app.models.material import MaterialVersion

    stmt = (
        select(Material, MaterialVersion)
        .options(selectinload(Material.directory))
        .join(ViewHistory, ViewHistory.material_id == Material.id)
        .outerjoin(
            MaterialVersion,
            (Material.id == MaterialVersion.material_id)
            & (Material.current_version == MaterialVersion.version_number),
        )
        .where(ViewHistory.user_id == uid)
        .order_by(ViewHistory.viewed_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)

    return [
        material_orm_to_dict(material, current_user_id=uid, current_version=version)
        for material, version in result.all()
    ]


async def get_user_contributions(
    db: AsyncSession,
    user_id: str,
    contribution_type: str,
    limit: int,
    offset: int,
    current_user_id: uuid.UUID | None = None,
) -> tuple[list[PullRequest] | list[dict[str, typing.Any]] | list[Annotation], int]:
    uid = uuid.UUID(str(user_id))
    from sqlalchemy.orm import selectinload

    if contribution_type == "prs":
        pr_base = select(PullRequest).where(PullRequest.author_id == uid)
        count_result = await db.execute(select(func.count()).select_from(pr_base.subquery()))
        total = count_result.scalar_one()
        result = await db.execute(
            pr_base.options(selectinload(PullRequest.author))
            .order_by(PullRequest.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total
    elif contribution_type == "materials":
        from app.models.material import MaterialVersion

        mat_base = select(Material).where(Material.author_id == uid)
        count_result = await db.execute(select(func.count()).select_from(mat_base.subquery()))
        total = count_result.scalar_one()
        result = await db.execute(
            select(Material, MaterialVersion)
            .where(Material.author_id == uid)
            .options(selectinload(Material.directory))
            .outerjoin(
                MaterialVersion,
                (Material.id == MaterialVersion.material_id)
                & (Material.current_version == MaterialVersion.version_number),
            )
            .order_by(Material.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        materials_out = [
            material_orm_to_dict(material, current_user_id=current_user_id, current_version=version)
            for material, version in result.all()
        ]
        return materials_out, total
    elif contribution_type == "annotations":
        ann_base = select(Annotation).where(Annotation.author_id == uid)
        count_result = await db.execute(select(func.count()).select_from(ann_base.subquery()))
        total = count_result.scalar_one()
        result = await db.execute(
            ann_base.options(selectinload(Annotation.author))
            .order_by(Annotation.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total
    else:
        raise BadRequestError("type must be one of: prs, materials, annotations")


UNSET: typing.Any = object()


async def update_user_profile(
    db: AsyncSession,
    user: User,
    display_name: str | None = UNSET,
    bio: str | None = UNSET,
    academic_year: str | None = UNSET,
    avatar_url: str | None = UNSET,
    auto_approve: bool | None = UNSET,
) -> User:
    if display_name is not UNSET and display_name is not None:
        user.display_name = display_name
    if bio is not UNSET:
        user.bio = bio
    if academic_year is not UNSET:
        user.academic_year = academic_year
    if auto_approve is not UNSET and auto_approve is not None:
        user.auto_approve = auto_approve

    if avatar_url is not UNSET and avatar_url != user.avatar_url:
        if avatar_url is None:
            # Clear avatar
            if user.avatar_url and user.avatar_url.startswith("avatars/"):
                await delete_object(user.avatar_url)
            user.avatar_url = None
        else:
            final_url = avatar_url

            # Handle new avatar from quarantine
            if avatar_url.startswith("quarantine/"):
                # 1. Security check: Verify ownership and existence
                stmt = select(Upload).where(
                    Upload.quarantine_key == avatar_url, Upload.user_id == user.id
                )
                res = await db.execute(stmt)
                upload_rec = res.scalar_one_or_none()

                if not upload_rec:
                    raise BadRequestError("Invalid avatar upload key or unauthorized")

                # 2. Process and Compress (Synchronous-ish)
                import tempfile
                import uuid as uuid_pkg
                from pathlib import Path

                with tempfile.TemporaryDirectory() as tmp_dir:
                    local_input = Path(tmp_dir) / "input_avatar"
                    await download_file(avatar_url, local_input)

                    try:
                        processed_path = await asyncio.to_thread(process_avatar, local_input)
                        try:
                            # 3. Upload to permanent avatars/ prefix
                            avatar_uuid = uuid_pkg.uuid4()
                            new_key = f"avatars/{user.id}/{avatar_uuid}.webp"

                            with open(processed_path, "rb") as f:
                                await upload_file(
                                    f.read(),
                                    new_key,
                                    content_type="image/webp",
                                    content_disposition="inline",  # Avatars should be viewable inline
                                )
                            final_url = new_key
                        finally:
                            if processed_path.exists():
                                processed_path.unlink()
                    except Exception as exc:
                        logger.error("Avatar processing failed: %s", exc)
                        raise BadRequestError(f"Failed to process avatar: {exc}")

                # 4. Cleanup quarantine
                await delete_object(avatar_url)

            # Delete old avatar from permanent storage if it's being replaced
            if (
                user.avatar_url
                and user.avatar_url.startswith("avatars/")
                and user.avatar_url != final_url
            ):
                await delete_object(user.avatar_url)

            if final_url.startswith("quarantine/"):
                # Safety: never let a quarantine URL into the User model
                raise BadRequestError("Cannot set avatar to unscanned quarantine key")

            user.avatar_url = final_url

    await db.flush()
    return user


async def export_user_data(db: AsyncSession, user: User) -> dict[str, typing.Any]:
    uid = user.id

    prs_result = await db.execute(select(PullRequest).where(PullRequest.author_id == uid))
    prs = prs_result.scalars().all()

    annotations_result = await db.execute(select(Annotation).where(Annotation.author_id == uid))
    annotations = annotations_result.scalars().all()

    comments_result = await db.execute(select(Comment).where(Comment.author_id == uid))
    comments = comments_result.scalars().all()

    pr_comments_result = await db.execute(select(PRComment).where(PRComment.author_id == uid))
    pr_comments = pr_comments_result.scalars().all()

    flags_result = await db.execute(select(Flag).where(Flag.reporter_id == uid))
    flags = flags_result.scalars().all()

    notifications_result = await db.execute(select(Notification).where(Notification.user_id == uid))
    notifications = notifications_result.scalars().all()

    view_history_result = await db.execute(select(ViewHistory).where(ViewHistory.user_id == uid))
    view_history = view_history_result.scalars().all()

    from app.models.material import MaterialFavourite

    favourites_result = await db.execute(
        select(MaterialFavourite).where(MaterialFavourite.user_id == uid)
    )
    favourites = favourites_result.scalars().all()

    return {
        "profile": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "bio": user.bio,
            "academic_year": user.academic_year,
            "role": user.role.value,
            "avatar_url": user.avatar_url,
            "created_at": user.created_at.isoformat(),
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "auto_approve": user.auto_approve,
            "is_flagged": user.is_flagged,
        },
        "consent": {
            "gdpr_consent": user.gdpr_consent,
            "gdpr_consent_at": user.gdpr_consent_at.isoformat() if user.gdpr_consent_at else None,
        },
        "pull_requests": [
            {"id": str(pr.id), "title": pr.title, "type": pr.type, "status": pr.status.value}
            for pr in prs
        ],
        "annotations": [
            {"id": str(a.id), "body": a.body, "material_id": str(a.material_id)}
            for a in annotations
        ],
        "comments": [
            {"id": str(c.id), "body": c.body, "target_type": c.target_type} for c in comments
        ],
        "pr_comments": [
            {"id": str(pc.id), "body": pc.body, "pr_id": str(pc.pr_id)} for pc in pr_comments
        ],
        "flags": [
            {"id": str(f.id), "target_type": f.target_type, "reason": f.reason} for f in flags
        ],
        "notifications": [
            {
                "id": str(n.id),
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "read": n.read,
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ],
        "view_history": [
            {
                "id": str(vh.id),
                "material_id": str(vh.material_id),
                "viewed_at": vh.viewed_at.isoformat(),
            }
            for vh in view_history
        ],
        "favourites": [
            {
                "id": str(fav.id),
                "material_id": str(fav.material_id),
                "created_at": fav.created_at.isoformat(),
            }
            for fav in favourites
        ],
    }


async def hard_delete_user(db: AsyncSession, user: User) -> None:
    from sqlalchemy import delete

    # 1. Delete avatar from storage
    if user.avatar_url:
        await delete_object(user.avatar_url)

    # 2. Cleanup orphaned Upload records (since they might not have a formal FK)
    await db.execute(delete(Upload).where(Upload.user_id == user.id))

    # 3. Delete the user — related rows (annotations, comments, PRs) are handled
    #    by DB-level ON DELETE CASCADE / SET NULL constraints, not ORM cascade.
    await db.execute(delete(User).where(User.id == user.id))
    await db.flush()
