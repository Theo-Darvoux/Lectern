import contextlib
import re
import uuid

from meilisearch_python_sdk.models.search import SearchParams
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.meilisearch import meili_search_client
from app.models.directory import DirectoryLike
from app.models.material import MaterialLike

# Allowlist for the ?type= filter — only alphanumeric, dash, underscore.
_SAFE_TYPE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")


async def perform_search(
    db: AsyncSession,
    query: str,
    page: int = 1,
    limit: int = 10,
    current_user_id: uuid.UUID | None = None,
    directory_id: uuid.UUID | None = None,
    type_filter: str | None = None,
) -> dict:  # type: ignore[type-arg]
    if not query.strip():
        return {"items": [], "total": 0, "page": page, "limit": limit}

    offset = (page - 1) * limit

    # Build per-index filter lists
    material_filters: list[str] = []
    directory_filters: list[str] = []

    if directory_id is not None:
        # directory_id scopes materials to a specific parent directory
        material_filters.append(f'directory_id = "{directory_id}"')

    if type_filter is not None:
        if not _SAFE_TYPE_RE.match(type_filter):
            return {"items": [], "total": 0, "page": page, "limit": limit}
        material_filters.append(f'type = "{type_filter}"')
        directory_filters.append(f'type = "{type_filter}"')

    mat_params = SearchParams(
        index_uid="materials",
        q=query,
        offset=offset,
        limit=limit,
        filter=material_filters or None,  # type: ignore[arg-type]
    )
    dir_params = SearchParams(
        index_uid="directories",
        q=query,
        offset=offset,
        limit=limit,
        filter=directory_filters or None,  # type: ignore[arg-type]
    )

    results = await meili_search_client.multi_search([mat_params, dir_params])
    materials_res = results[0]  # type: ignore[index]
    directories_res = results[1]  # type: ignore[index]

    # Scope like lookups to only the hits returned — avoids loading full like history.
    hit_material_ids: set[uuid.UUID] = set()
    hit_directory_ids: set[uuid.UUID] = set()
    for hit in materials_res.hits:
        with contextlib.suppress(KeyError, ValueError):
            hit_material_ids.add(uuid.UUID(hit["id"]))
    for hit in directories_res.hits:
        with contextlib.suppress(KeyError, ValueError):
            hit_directory_ids.add(uuid.UUID(hit["id"]))

    liked_material_ids: set[uuid.UUID] = set()
    liked_directory_ids: set[uuid.UUID] = set()
    if current_user_id:
        if hit_material_ids:
            m_stmt = select(MaterialLike.material_id).where(
                MaterialLike.user_id == current_user_id,
                MaterialLike.material_id.in_(hit_material_ids),
            )
            m_res = await db.execute(m_stmt)
            liked_material_ids = {row[0] for row in m_res.all()}

        if hit_directory_ids:
            d_stmt = select(DirectoryLike.directory_id).where(
                DirectoryLike.user_id == current_user_id,
                DirectoryLike.directory_id.in_(hit_directory_ids),
            )
            d_res = await db.execute(d_stmt)
            liked_directory_ids = {row[0] for row in d_res.all()}

    items = []

    for hit in materials_res.hits:
        hit["search_type"] = "material"
        hit_id_str = hit.get("id")
        if hit_id_str:
            try:
                hit["is_liked"] = uuid.UUID(hit_id_str) in liked_material_ids
            except ValueError:
                hit["is_liked"] = False
        items.append(hit)

    for hit in directories_res.hits:
        hit["search_type"] = "directory"
        hit_id_str = hit.get("id")
        if hit_id_str:
            try:
                hit["is_liked"] = uuid.UUID(hit_id_str) in liked_directory_ids
            except ValueError:
                hit["is_liked"] = False
        items.append(hit)

    total = (materials_res.estimated_total_hits or 0) + (directories_res.estimated_total_hits or 0)

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
    }
