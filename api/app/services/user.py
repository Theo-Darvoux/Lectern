import logging
import typing
import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.core.database.post_commit import (
    PostCommitKey,
    add_post_commit_job,
    register_transaction_callbacks,
)
from app.core.security.isolated_parser import process_avatar_isolated
from app.core.security.processing_paths import processing_temp_dir
from app.core.storage.facade import delete_object, download_file_raw, upload_file
from app.models.annotation import Annotation
from app.models.comment import Comment
from app.models.flag import Flag
from app.models.material import Material
from app.models.notification import Notification
from app.models.pull_request import PRComment, PullRequest
from app.models.upload import Upload
from app.models.user import User
from app.models.view_history import ViewHistory
from app.services.avatar import is_owned_avatar_storage_key
from app.services.material import get_liked_favourited_sets, material_orm_to_dict

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


async def mark_tutorial_complete(db: AsyncSession, user: User, tutorial_id: str) -> User:
    """Idempotently record that the user finished a tutorial."""
    completed = list(user.completed_tutorials or [])
    if tutorial_id not in completed:
        completed.append(tutorial_id)
        # Reassign so SQLAlchemy detects the change on the JSON column.
        user.completed_tutorials = completed
        await db.flush()
    return user


async def reset_tutorials(db: AsyncSession, user: User) -> User:
    """Clear all completed-tutorial flags so auto-launch tours show again."""
    if user.completed_tutorials:
        user.completed_tutorials = []
        await db.flush()
    return user


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    uid = uuid.UUID(str(user_id))
    result = await db.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()


async def get_user_stats(db: AsyncSession, user_id: str) -> dict[str, int]:
    uid = uuid.UUID(str(user_id))
    from app.models.pull_request import PRStatus

    # All PR counts in a single pass with conditional aggregation. (AsyncSession
    # can't run queries concurrently, so folding queries beats asyncio.gather.)
    prs_total, pr_approved, open_pr_count = (
        await db.execute(
            select(
                func.count(PullRequest.id),
                func.coalesce(
                    func.sum(case((PullRequest.status == PRStatus.APPROVED, 1), else_=0)), 0
                ),
                func.coalesce(func.sum(case((PullRequest.status == PRStatus.OPEN, 1), else_=0)), 0),
            ).where(PullRequest.author_id == uid)
        )
    ).one()
    annotations_count = await db.scalar(select(func.count()).where(Annotation.author_id == uid))
    comments_count = await db.scalar(select(func.count()).where(Comment.author_id == uid))

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
    rows = (await db.execute(stmt)).all()
    liked_ids, favourited_ids = await get_liked_favourited_sets(
        db, uid, [material.id for material, _ in rows]
    )

    return [
        material_orm_to_dict(
            material,
            current_user_id=uid,
            current_version=version,
            is_liked=material.id in liked_ids,
            is_favourited=material.id in favourited_ids,
        )
        for material, version in rows
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
        rows = result.all()
        liked_ids, favourited_ids = await get_liked_favourited_sets(
            db, current_user_id, [material.id for material, _ in rows]
        )
        materials_out = [
            material_orm_to_dict(
                material,
                current_user_id=current_user_id,
                current_version=version,
                is_liked=material.id in liked_ids,
                is_favourited=material.id in favourited_ids,
            )
            for material, version in rows
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
    avatar_upload_id: uuid.UUID | str | None = UNSET,
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

    if avatar_url is not UNSET and avatar_upload_id is not UNSET:
        raise BadRequestError("Choose either avatar clear or avatar upload, not both")

    if avatar_url is not UNSET:
        # avatar_url is a server-owned output field. The only accepted client
        # mutation is explicit null to clear an existing avatar.
        if avatar_url is not None:
            raise BadRequestError("avatar_url is read-only; use avatar_upload_id")
        if is_owned_avatar_storage_key(user.avatar_url, user.id):
            add_post_commit_job(db, ("delete_storage_objects", [user.avatar_url]))
        user.avatar_url = None

    elif avatar_upload_id is not UNSET and avatar_upload_id is not None:
        upload_rec = await db.scalar(
            select(Upload)
            .where(
                Upload.upload_id == str(avatar_upload_id),
                Upload.user_id == user.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if upload_rec is None:
            raise BadRequestError("Invalid avatar upload or unauthorized")

        quarantine_key = upload_rec.quarantine_key
        expected_quarantine_prefix = f"quarantine/{user.id}/"
        if (
            upload_rec.status != "clean"
            or not upload_rec.mime_type
            or not upload_rec.mime_type.startswith("image/")
            or not upload_rec.final_key
            or not upload_rec.final_key.startswith("cas/")
            or int(upload_rec.cas_ref_count or 0) <= 0
            or not quarantine_key
            or not quarantine_key.startswith(expected_quarantine_prefix)
        ):
            raise BadRequestError("Avatar upload has not passed image security processing")

        # The CAS key is read only from the caller-owned Upload row. It is never
        # accepted from request data and never persisted as the avatar reference.
        import uuid as uuid_pkg
        from pathlib import Path

        with processing_temp_dir(prefix="avatar-") as tmp_dir:
            local_input = Path(tmp_dir) / "input_avatar"
            await download_file_raw(
                upload_rec.final_key,
                local_input,
                max_bytes=20 * 1024 * 1024,
            )

            try:
                processed_bytes = await process_avatar_isolated(local_input)
                avatar_uuid = uuid_pkg.uuid4()
                new_key = f"avatars/{user.id}/{avatar_uuid}.webp"

                async def _remove_uncommitted_avatar() -> None:
                    await delete_object(new_key)

                async def _avatar_commit_complete() -> None:
                    return None

                managed_transaction = register_transaction_callbacks(
                    db,
                    on_rollback=_remove_uncommitted_avatar,
                    on_commit=_avatar_commit_complete,
                )

                try:
                    await upload_file(
                        processed_bytes,
                        new_key,
                        content_type="image/webp",
                        content_disposition="inline",
                    )
                except Exception:
                    if not managed_transaction:
                        try:
                            await delete_object(new_key)
                        except Exception:
                            logger.exception(
                                "Failed to remove avatar after an uncertain upload failure"
                            )
                    raise
            except Exception as exc:
                logger.error("Avatar processing failed: %s", exc)
                raise BadRequestError(f"Failed to process avatar: {exc}")

        add_post_commit_job(db, ("delete_storage_objects", [quarantine_key]))

        if is_owned_avatar_storage_key(user.avatar_url, user.id) and user.avatar_url != new_key:
            add_post_commit_job(db, ("delete_storage_objects", [user.avatar_url]))

        if not is_owned_avatar_storage_key(new_key, user.id):
            raise RuntimeError("Generated avatar key escaped the user avatar namespace")
        user.avatar_url = new_key

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

    # Always enter the authority boundary before deleting. The caller may hold a
    # stale ORM object whose role changed after it was loaded; the service re-reads
    # authoritative role/deletion state under the shared lock and a row lock.
    from app.services.auth import ensure_admin_removal_safe

    await ensure_admin_removal_safe(db, user.id)

    # Durably release storage/CAS/quota resources only after the user deletion
    # commits. OAuth avatar URLs are external and never object-store keys.
    uploads = list((await db.scalars(select(Upload).where(Upload.user_id == user.id))).all())
    cas_references: list[dict[str, str]] = []
    storage_keys: set[str] = set()
    if user.avatar_url and user.avatar_url.startswith("avatars/"):
        storage_keys.add(user.avatar_url)
    quota_members: list[str] = []
    for upload in uploads:
        reference_sha = upload.content_sha256 or upload.sha256
        if upload.cas_ref_count > 0 and reference_sha:
            cas_references.append(
                {
                    "sha256": reference_sha,
                    "operation_id": f"hard-delete:upload:{upload.id}:release",
                }
            )
        for key in (upload.quarantine_key, upload.final_key):
            if key and not key.startswith("cas/"):
                storage_keys.add(key)
        quota_members.append(f"staging:{upload.user_id}:{upload.upload_id}")

    jobs = db.info.setdefault(PostCommitKey.JOBS, [])
    if cas_references:
        jobs.append(("release_cas_references", cas_references))
    if storage_keys:
        jobs.append(("delete_storage_objects", sorted(storage_keys)))
    if quota_members:
        jobs.append(("release_upload_quota", str(user.id), quota_members))

    # Cleanup orphaned Upload records (since they might not have a formal FK)
    await db.execute(delete(Upload).where(Upload.user_id == user.id))

    # Delete the user — related rows (annotations, comments, PRs) are handled
    #    by DB-level ON DELETE CASCADE / SET NULL constraints, not ORM cascade.
    await db.execute(delete(User).where(User.id == user.id))
    await db.flush()
