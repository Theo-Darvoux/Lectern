import uuid
from datetime import UTC, datetime

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.annotation import Annotation
from app.models.pull_request import PRStatus, PullRequest
from app.models.user import User, UserRole
from app.schemas.leaderboard import LeaderboardEntry, LeaderboardPeriod, LeaderboardResponse

APPROVED_CONTRIBUTION_POINTS = 10


def get_period_start(period: LeaderboardPeriod, now: datetime | None = None) -> datetime | None:
    if period == "all_time":
        return None

    current = now or datetime.now(UTC)
    if period == "month":
        return datetime(current.year, current.month, 1, tzinfo=UTC)

    if current.month >= 8:
        return datetime(current.year, 8, 1, tzinfo=UTC)
    if current.month >= 2:
        return datetime(current.year, 2, 1, tzinfo=UTC)
    return datetime(current.year - 1, 8, 1, tzinfo=UTC)


def _entry_from_row(row: object) -> LeaderboardEntry:
    mapping = row._mapping  # type: ignore[attr-defined]
    return LeaderboardEntry(
        rank=int(mapping["rank"]),
        user_id=mapping["user_id"],
        display_name=mapping["display_name"],
        avatar_url=mapping["avatar_url"],
        academic_year=mapping["academic_year"],
        approved_contributions=int(mapping["approved_contributions"]),
        annotations=int(mapping["annotations"]),
        score=int(mapping["score"]),
    )


async def get_leaderboard(
    db: AsyncSession,
    *,
    current_user_id: uuid.UUID,
    period: LeaderboardPeriod,
    academic_year: str | None,
    page: int,
    limit: int,
) -> LeaderboardResponse:
    period_start = get_period_start(period)

    pr_filters: list[ColumnElement[bool]] = [
        PullRequest.status == PRStatus.APPROVED,
        PullRequest.author_id.is_not(None),
        PullRequest.type != "revert",
        PullRequest.reverted_by_pr_id.is_(None),
    ]
    if period_start is not None:
        pr_filters.append(
            func.coalesce(
                PullRequest.approved_at,
                PullRequest.updated_at,
                PullRequest.created_at,
            )
            >= period_start
        )

    annotation_filters: list[ColumnElement[bool]] = [Annotation.author_id.is_not(None)]
    if period_start is not None:
        annotation_filters.append(Annotation.created_at >= period_start)

    approved = (
        select(
            PullRequest.author_id.label("user_id"),
            func.count(PullRequest.id).label("approved_contributions"),
        )
        .where(*pr_filters)
        .group_by(PullRequest.author_id)
        .subquery()
    )
    annotations = (
        select(
            Annotation.author_id.label("user_id"),
            func.count(Annotation.id).label("annotations"),
        )
        .where(*annotation_filters)
        .group_by(Annotation.author_id)
        .subquery()
    )

    approved_count = func.coalesce(approved.c.approved_contributions, 0)
    annotation_count = func.coalesce(annotations.c.annotations, 0)
    score = approved_count * APPROVED_CONTRIBUTION_POINTS

    score_query = (
        select(
            User.id.label("user_id"),
            User.display_name,
            User.avatar_url,
            User.academic_year,
            approved_count.label("approved_contributions"),
            annotation_count.label("annotations"),
            score.label("score"),
        )
        .outerjoin(approved, approved.c.user_id == User.id)
        .outerjoin(annotations, annotations.c.user_id == User.id)
        .where(
            User.deleted_at.is_(None),
            User.is_flagged.is_(False),
            User.role.not_in([UserRole.GUEST, UserRole.PENDING]),
            score > 0,
        )
    )
    if academic_year is not None:
        score_query = score_query.where(User.academic_year == academic_year)

    scores = score_query.cte("leaderboard_scores")
    ranked = select(
        scores,
        func.rank().over(order_by=scores.c.score.desc()).label("rank"),
        func.row_number()
        .over(
            order_by=(
                scores.c.score.desc(),
                scores.c.approved_contributions.desc(),
                scores.c.display_name.asc(),
                scores.c.user_id.asc(),
            )
        )
        .label("position"),
    ).cte("ranked_leaderboard")

    result_columns = (
        ranked.c.user_id,
        ranked.c.display_name,
        ranked.c.avatar_url,
        ranked.c.academic_year,
        ranked.c.approved_contributions,
        ranked.c.annotations,
        ranked.c.score,
        ranked.c.rank,
        ranked.c.position,
    )
    offset = (page - 1) * limit
    page_rows = select(
        *result_columns,
        literal("page").label("row_kind"),
        literal(0).label("total"),
    ).where(
        ranked.c.position > offset,
        ranked.c.position <= offset + limit,
    )
    current_user_row = select(
        *result_columns,
        literal("current_user").label("row_kind"),
        literal(0).label("total"),
    ).where(ranked.c.user_id == current_user_id)
    total_row = select(
        *[literal(None) for _ in result_columns],
        literal("total").label("row_kind"),
        func.count(ranked.c.user_id).label("total"),
    )
    combined = union_all(page_rows, current_user_row, total_row).subquery()
    rows = (
        await db.execute(
            select(combined).order_by(combined.c.row_kind.desc(), combined.c.position)
        )
    ).all()

    page_entries = [
        _entry_from_row(row) for row in rows if row._mapping["row_kind"] == "page"
    ]
    current_entry = next(
        (
            _entry_from_row(row)
            for row in rows
            if row._mapping["row_kind"] == "current_user"
        ),
        None,
    )
    total = next(
        int(row._mapping["total"])
        for row in rows
        if row._mapping["row_kind"] == "total"
    )

    return LeaderboardResponse(
        items=page_entries,
        current_user=current_entry,
        total=total,
        page=page,
        pages=max(1, (total + limit - 1) // limit),
        period=period,
        academic_year=academic_year,
    )
