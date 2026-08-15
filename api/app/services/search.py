import re
import uuid
from typing import Any

from meilisearch_python_sdk.models.search import SearchParams
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.core.events.meilisearch import SEARCH_MAX_TOTAL_HITS, get_search_client
from app.models.directory import Directory, DirectoryLike
from app.models.material import Material, MaterialLike

# Allowlist for the ?type= filter — only alphanumeric, dash, underscore.
_SAFE_TYPE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")

# Public search must not expose Meilisearch-only counts or let stale documents
# consume page slots. Scan the explicitly configured Meilisearch pagination
# horizon, validate IDs against PostgreSQL, then paginate/count only authoritative
# live hits. Queries that fill the complete horizon fail closed rather than
# returning a potentially partial total.
_SEARCH_SCAN_BATCH = 250
_SEARCH_SCAN_MAX_HITS_PER_INDEX = SEARCH_MAX_TOTAL_HITS


async def _authoritative_live_ids(
    db: AsyncSession,
    model: type[Material] | type[Directory],
    ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    """Return only IDs that still exist and are live in PostgreSQL.

    The global soft-delete loader criteria applies here, so deleted rows are
    excluded. Missing rows are excluded too, which also fails closed for stale
    Meilisearch documents left behind after a hard delete or historical drift.
    """
    if not ids:
        return set()
    return set(await db.scalars(select(model.id).where(model.id.in_(ids))))


def _parse_hit_id(hit: dict[str, Any]) -> uuid.UUID | None:
    raw = hit.get("id")
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError):
        return None


async def _live_hits_for_batch(
    db: AsyncSession,
    model: type[Material] | type[Directory],
    hits: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], uuid.UUID]]:
    parsed = [(hit, parsed_id) for hit in hits if (parsed_id := _parse_hit_id(hit)) is not None]
    live_ids = await _authoritative_live_ids(db, model, {parsed_id for _, parsed_id in parsed})
    return [(hit, parsed_id) for hit, parsed_id in parsed if parsed_id in live_ids]


def _search_params(
    *,
    index_uid: str,
    query: str,
    offset: int,
    limit: int,
    filters: list[str],
) -> SearchParams:
    return SearchParams(
        index_uid=index_uid,
        q=query,
        offset=offset,
        limit=limit,
        filter=filters or None,  # type: ignore[arg-type]
    )


async def _authoritative_search_hits(
    db: AsyncSession,
    query: str,
    material_filters: list[str],
    directory_filters: list[str],
) -> tuple[list[tuple[dict[str, Any], uuid.UUID]], list[tuple[dict[str, Any], uuid.UUID]]]:
    """Return all bounded live Meili hits, preserving per-index ranking order.

    Meilisearch remains the relevance engine, but PostgreSQL is the liveness
    authority. We deliberately filter before client pagination and count.
    """
    client = get_search_client()
    live_materials: list[tuple[dict[str, Any], uuid.UUID]] = []
    live_directories: list[tuple[dict[str, Any], uuid.UUID]] = []
    scan_offset = 0

    while scan_offset < _SEARCH_SCAN_MAX_HITS_PER_INDEX:
        results = await client.multi_search(
            [
                _search_params(
                    index_uid="materials",
                    query=query,
                    offset=scan_offset,
                    limit=_SEARCH_SCAN_BATCH,
                    filters=material_filters,
                ),
                _search_params(
                    index_uid="directories",
                    query=query,
                    offset=scan_offset,
                    limit=_SEARCH_SCAN_BATCH,
                    filters=directory_filters,
                ),
            ]
        )
        materials_res = results[0]  # type: ignore[index]
        directories_res = results[1]  # type: ignore[index]

        if scan_offset == 0:
            material_estimate = int(materials_res.estimated_total_hits or 0)
            directory_estimate = int(directories_res.estimated_total_hits or 0)
            if (
                material_estimate > _SEARCH_SCAN_MAX_HITS_PER_INDEX
                or directory_estimate > _SEARCH_SCAN_MAX_HITS_PER_INDEX
            ):
                # Do not return the unvalidated estimate: that would recreate the
                # deleted-keyword oracle. The caller only learns the query is too broad.
                raise BadRequestError("Search query is too broad; add more terms or filters")

        material_hits = list(materials_res.hits)
        directory_hits = list(directories_res.hits)
        live_materials.extend(await _live_hits_for_batch(db, Material, material_hits))
        live_directories.extend(await _live_hits_for_batch(db, Directory, directory_hits))

        if len(material_hits) < _SEARCH_SCAN_BATCH and len(directory_hits) < _SEARCH_SCAN_BATCH:
            break
        scan_offset += _SEARCH_SCAN_BATCH
    else:
        # Defensive fallback if the server under-reports estimated_total_hits but
        # still returns a full final page at our bound. Never emit a partial count.
        raise BadRequestError("Search query is too broad; add more terms or filters")

    return live_materials, live_directories


async def perform_search(
    db: AsyncSession,
    query: str,
    page: int = 1,
    limit: int = 10,
    current_user_id: uuid.UUID | None = None,
    directory_id: uuid.UUID | None = None,
    type_filter: str | None = None,
) -> dict[str, Any]:
    if not query.strip():
        return {"items": [], "total": 0, "page": page, "limit": limit}

    offset = (page - 1) * limit

    material_filters: list[str] = []
    directory_filters: list[str] = []

    if directory_id is not None:
        material_filters.append(f'directory_id = "{directory_id}"')

    if type_filter is not None:
        if not _SAFE_TYPE_RE.match(type_filter):
            return {"items": [], "total": 0, "page": page, "limit": limit}
        material_filters.append(f'type = "{type_filter}"')
        directory_filters.append(f'type = "{type_filter}"')

    live_material_pairs, live_directory_pairs = await _authoritative_search_hits(
        db,
        query,
        material_filters,
        directory_filters,
    )

    # Preserve the existing materials-first ordering, but apply pagination only
    # after stale/malformed/deleted hits have been removed.
    all_live_pairs: list[tuple[str, dict[str, Any], uuid.UUID]] = [
        *(("material", hit, parsed_id) for hit, parsed_id in live_material_pairs),
        *(("directory", hit, parsed_id) for hit, parsed_id in live_directory_pairs),
    ]
    total = len(all_live_pairs)
    page_pairs = all_live_pairs[offset : offset + limit]

    page_material_ids = {parsed_id for kind, _, parsed_id in page_pairs if kind == "material"}
    page_directory_ids = {parsed_id for kind, _, parsed_id in page_pairs if kind == "directory"}

    liked_material_ids: set[uuid.UUID] = set()
    liked_directory_ids: set[uuid.UUID] = set()
    if current_user_id:
        if page_material_ids:
            m_stmt = select(MaterialLike.material_id).where(
                MaterialLike.user_id == current_user_id,
                MaterialLike.material_id.in_(page_material_ids),
            )
            m_res = await db.execute(m_stmt)
            liked_material_ids = {row[0] for row in m_res.all()}

        if page_directory_ids:
            d_stmt = select(DirectoryLike.directory_id).where(
                DirectoryLike.user_id == current_user_id,
                DirectoryLike.directory_id.in_(page_directory_ids),
            )
            d_res = await db.execute(d_stmt)
            liked_directory_ids = {row[0] for row in d_res.all()}

    items: list[dict[str, Any]] = []
    for kind, hit, parsed_id in page_pairs:
        hit["search_type"] = kind
        if kind == "material":
            hit["is_liked"] = parsed_id in liked_material_ids
        else:
            hit["is_liked"] = parsed_id in liked_directory_ids
        items.append(hit)

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
    }
