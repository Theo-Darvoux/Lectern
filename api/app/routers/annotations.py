import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.common.exceptions import NotFoundError
from app.core.database.database import get_db
from app.core.events.limiter import limiter
from app.core.events.sse import (
    broadcast_to_topic,
    register_topic_queue,
    sse_event_stream,
    unregister_topic_queue,
)
from app.dependencies.auth import CurrentUser, OnboardedUser
from app.models.material import Material
from app.schemas.annotation import (
    AnnotationCreateIn,
    AnnotationOut,
    AnnotationUpdateIn,
    ThreadOut,
)
from app.schemas.common import CursorPaginatedResponse
from app.services.annotation import (
    create_annotation,
    delete_annotation,
    get_annotations,
    update_annotation,
)

material_annotations_router = APIRouter(prefix="/api/materials", tags=["annotations"])
annotations_router = APIRouter(prefix="/api/annotations", tags=["annotations"])


@material_annotations_router.get(
    "/{material_id}/annotations",
    response_model=CursorPaginatedResponse[ThreadOut],
)
@limiter.limit("120/minute")
async def list_annotations(
    request: Request,
    material_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    version: Annotated[int | None, Query()] = None,
    doc_page: Annotated[int | None, Query(alias="docPage")] = None,
) -> CursorPaginatedResponse[ThreadOut]:
    roots, total, next_cursor = await get_annotations(
        db, material_id, limit, cursor, version, doc_page
    )
    threads = [
        ThreadOut(
            root=AnnotationOut.model_validate(r),
            replies=[AnnotationOut.model_validate(rep) for rep in r._replies],
        )
        for r in roots
    ]
    return CursorPaginatedResponse[ThreadOut](
        items=threads,
        total=total,
        next_cursor=next_cursor,
    )


@material_annotations_router.post(
    "/{material_id}/annotations",
    response_model=AnnotationOut,
    status_code=201,
)
@limiter.limit("10/minute")
async def add_annotation(
    request: Request,
    material_id: str,
    data: AnnotationCreateIn,
    user: OnboardedUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnnotationOut:
    annotation = await create_annotation(
        db,
        material_id,
        user.id,
        data.body,
        data.selection_text,
        data.position_data.model_dump(mode="json") if data.position_data else None,
        data.page,
        data.reply_to_id,
    )
    out = AnnotationOut.model_validate(annotation)
    broadcast_to_topic(
        material_id,
        {
            "type": "annotation_created",
            "annotation": out.model_dump(mode="json"),
        },
    )
    return out


@annotations_router.patch("/{annotation_id}", response_model=AnnotationOut)
async def edit_annotation(
    annotation_id: str,
    data: AnnotationUpdateIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnnotationOut:
    annotation = await update_annotation(db, annotation_id, user, data.body)
    out = AnnotationOut.model_validate(annotation)
    broadcast_to_topic(
        str(annotation.material_id),
        {
            "type": "annotation_updated",
            "annotation": out.model_dump(mode="json"),
        },
    )
    return out


@annotations_router.delete("/{annotation_id}", status_code=204)
async def remove_annotation(
    annotation_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    material_id, deleted_id, thread_id = await delete_annotation(db, annotation_id, user)
    broadcast_to_topic(
        str(material_id),
        {
            "type": "annotation_deleted",
            "id": str(deleted_id),
            "thread_id": str(thread_id),
        },
    )


@material_annotations_router.get("/{material_id}/sse")
@limiter.limit("20/minute")
async def material_event_stream(
    request: Request,
    material_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EventSourceResponse:
    try:
        mid = uuid.UUID(material_id)
    except ValueError:
        raise NotFoundError("Material not found")

    result = await db.execute(select(Material).where(Material.id == mid))
    if not result.scalar_one_or_none():
        raise NotFoundError("Material not found")

    queue = register_topic_queue(material_id)
    return EventSourceResponse(
        sse_event_stream(
            queue,
            cleanup=lambda: unregister_topic_queue(material_id, queue),
        ),
        headers={"X-Accel-Buffering": "no"},
    )
