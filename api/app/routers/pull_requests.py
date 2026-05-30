import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sse_starlette.sse import EventSourceResponse

from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.limiter import limiter
from app.core.redis import get_redis
from app.core.sse import register_topic_queue, sse_event_stream, unregister_topic_queue
from app.dependencies.auth import (
    SSEUser,
    get_current_user,
    require_moderator,
    require_not_guest,
    require_role,
)
from app.dependencies.pagination import set_pagination_headers
from app.models.pull_request import PRComment, PullRequest
from app.models.user import User, UserRole
from app.schemas.pull_request import (
    PRCommentCreate,
    PRCommentOut,
    PullRequestCreate,
    PullRequestOut,
    RejectRequest,
)
from app.services.pr import (
    approve_pr_service,
    cancel_pr_service,
    create_pull_request_service,
    get_pr_diff_service,
    get_pr_preview_service,
    list_prs_for_item_service,
    list_prs_service,
    reject_pr_service,
    revert_pr_service,
)

logger = logging.getLogger("wikint")

router = APIRouter(prefix="/api/pull-requests", tags=["pull-requests"])


@router.get("/sse")
async def pull_request_sse(user: SSEUser) -> EventSourceResponse:
    """Per-user SSE stream for PR list updates (pr_opened / pr_closed events)."""
    topic = f"pr_updates:{user.id}"
    queue = register_topic_queue(topic)
    return EventSourceResponse(
        sse_event_stream(queue, cleanup=lambda: unregister_topic_queue(topic, queue)),
        headers={"X-Accel-Buffering": "no"},
    )


@router.post("", response_model=PullRequestOut, status_code=201)
async def create_pull_request(
    data: PullRequestCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: Annotated[User, Depends(get_current_user)],
) -> PullRequestOut:
    pr = await create_pull_request_service(db, data, current_user, redis=redis)
    await db.commit()
    return PullRequestOut.model_validate(pr)


@router.get("", response_model=list[PullRequestOut])
async def list_pull_requests(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_not_guest())],
    status: str | None = None,
    type: str | None = None,
    author_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
) -> list[PullRequestOut]:
    prs, total_count = await list_prs_service(db, status, type, author_id, page, limit)
    set_pagination_headers(response, total_count)
    return [PullRequestOut.model_validate(pr) for pr in prs]


@router.get("/for-item", response_model=list[PullRequestOut])
async def list_pull_requests_for_item(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_not_guest())],
    target_type: str = Query(alias="targetType"),
    target_id: str = Query(alias="targetId"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
) -> list[PullRequestOut]:
    """
    Search within the JSONB array payload for operations referencing
    the given target item.
    """
    prs, total_count = await list_prs_for_item_service(db, target_type, target_id, page, limit)
    set_pagination_headers(response, total_count)

    return [PullRequestOut.model_validate(pr) for pr in prs]


@router.get("/{id}", response_model=PullRequestOut)
async def get_pull_request(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_not_guest())],
) -> PullRequestOut:
    pr = await db.scalar(
        select(PullRequest).options(joinedload(PullRequest.author)).where(PullRequest.id == id)
    )
    if not pr:
        raise NotFoundError("Pull request not found")
    return PullRequestOut.model_validate(pr)


@router.post("/{id}/approve")
async def approve_pull_request(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_moderator())],
) -> dict[str, Any]:
    await approve_pr_service(db, id, current_user)
    return {"status": "ok"}


@router.post("/{id}/reject")
async def reject_pull_request(
    id: uuid.UUID,
    data: RejectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_moderator())],
) -> dict[str, Any]:
    await reject_pr_service(db, id, data.reason, current_user)
    return {"status": "ok"}


@router.post("/{id}/revert", response_model=PullRequestOut, status_code=201)
async def revert_pull_request(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(
            require_role(
                UserRole.BUREAU,
                UserRole.VIEUX,
                message="Only administrators can revert contributions",
            )
        ),
    ],
) -> PullRequestOut:
    revert = await revert_pr_service(db, id, current_user)
    return PullRequestOut.model_validate(revert)


@router.post("/{id}/cancel")
async def cancel_pull_request(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Author cancels their own open pull request."""
    await cancel_pr_service(db, id, current_user)
    return {"status": "ok"}


@router.get("/{id}/diff")
async def get_pull_request_diff(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_not_guest())],
) -> dict[str, Any]:
    diff = await get_pr_diff_service(db, id)
    return diff


@router.get("/{id}/preview")
async def get_pull_request_preview(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_not_guest())],
    op_index: int = Query(0, ge=0, alias="opIndex"),
) -> dict[str, Any]:
    preview = await get_pr_preview_service(db, id, op_index, current_user)
    return preview


@router.get("/{id}/comments", response_model=list[PRCommentOut])
async def list_pull_request_comments(
    id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_not_guest())],
) -> list[PRCommentOut]:
    stmt = (
        select(PRComment)
        .options(joinedload(PRComment.author))
        .where(PRComment.pr_id == id)
        .order_by(PRComment.created_at)
    )
    res = await db.execute(stmt)
    comments = list(res.scalars().all())
    return [PRCommentOut.model_validate(c) for c in comments]


@router.post("/{id}/comments", response_model=PRCommentOut)
@limiter.limit("10/minute")
async def create_pull_request_comment(
    request: Request,
    id: uuid.UUID,
    data: PRCommentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PRCommentOut:
    pr = await db.scalar(select(PullRequest).where(PullRequest.id == id))
    if not pr:
        raise NotFoundError("Pull request not found")

    c = PRComment(
        id=uuid.uuid4(),
        pr_id=id,
        author_id=current_user.id,
        body=data.body,
        parent_id=data.parent_id,
    )
    db.add(c)
    await db.flush()
    await db.refresh(c, ["author"])

    if data.parent_id:
        parent = await db.scalar(select(PRComment).where(PRComment.id == data.parent_id))
        if parent and parent.author_id and parent.author_id != current_user.id:
            from app.services.notification import notify_user

            await notify_user(
                db,
                parent.author_id,
                "pr_comment_reply",
                f'Someone replied to your comment on "{pr.title}"',
                link=f"/pull-requests/{pr.id}",
            )

    return PRCommentOut.model_validate(c)
