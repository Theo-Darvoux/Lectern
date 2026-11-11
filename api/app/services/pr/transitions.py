"""Review transitions of an open pull request: approve, reject, cancel, revert."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.common.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.core.database.post_commit import PostCommitKey
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User
from app.services.notification import notify_user
from app.services.pr.apply import apply_pr
from app.services.pr.operations import _pr_directory_topics
from app.services.pr.resources import _cleanup_pr_resources, _lock_and_validate_pr_cas_files
from app.services.pr.revert import revert_pr


async def _lock_open_pr_for_transition(db: AsyncSession, pr_id: uuid.UUID) -> PullRequest:
    """Lock and refresh an OPEN PR before any terminal transition."""
    pr = await db.scalar(
        select(PullRequest)
        .where(PullRequest.id == pr_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if pr is None:
        raise NotFoundError("Pull request not found")
    if pr.status != PRStatus.OPEN:
        raise BadRequestError("This contribution is no longer open")
    return pr


async def approve_pr_service(db: AsyncSession, pr_id: uuid.UUID, reviewer: User) -> PullRequest:
    """Approve and apply a contribution."""
    pr = await _lock_open_pr_for_transition(db, pr_id)

    await _lock_and_validate_pr_cas_files(db, pr)

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
    pr = await _lock_open_pr_for_transition(db, pr_id)

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
    pr = await _lock_open_pr_for_transition(db, pr_id)

    if pr.author_id != current_user.id:
        raise ForbiddenError("Only the author can cancel this contribution")

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
    """Validate and execute a serialized revert for an approved PR."""
    pr = await db.scalar(
        select(PullRequest)
        .where(PullRequest.id == pr_id)
        .options(selectinload(PullRequest.author))
        .with_for_update()
        .execution_options(populate_existing=True)
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
