import re
import uuid
from typing import Any

from meilisearch_python_sdk.models.search import SearchParams
from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.events.meilisearch import SEARCH_MAX_TOTAL_HITS, get_search_client
from app.models.directory import Directory, DirectoryLike
from app.models.material import Material, MaterialLike

# Allowlist for the ?type= filter — only alphanumeric, dash, underscore.
_SAFE_TYPE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")

# Public search must not expose Meilisearch-only counts or let stale documents
# consume page slots. Scan the explicitly configured Meilisearch pagination
# horizon, validate IDs against PostgreSQL, then paginate/count only authoritative
# live hits.
_SEARCH_SCAN_BATCH = 250
_SEARCH_SCAN_MAX_HITS_PER_INDEX = SEARCH_MAX_TOTAL_HITS
_DIRECTORY_SCOPE_MAX_DEPTH = 64


async def _directory_scope_ids(
    db: AsyncSession, directory_id: uuid.UUID
) -> set[uuid.UUID]:
    base = (
        select(Directory.id.label("id"), literal(0).label("depth"))
        .where(Directory.id == directory_id, Directory.deleted_at.is_(None))
        .cte("search_directory_scope", recursive=True)
    )
    child = aliased(Directory)
    scope = base.union_all(
        select(child.id, (base.c.depth + 1).label("depth")).where(
            child.parent_id == base.c.id,
            child.deleted_at.is_(None),
            base.c.depth < _DIRECTORY_SCOPE_MAX_DEPTH,
        )
    )
    return set(await db.scalars(select(scope.c.id)))


def _uuid_in_filter(attribute: str, values: set[uuid.UUID]) -> str:
    if not values:
        return f'{attribute} = "__missing__"'
    rendered = ", ".join(f'"{value}"' for value in sorted(values, key=str))
    return f"{attribute} IN [{rendered}]"


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
        show_ranking_score=True,
    )


def _normalized_label(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _mix_ranked_hits(
    query: str,
    materials: list[tuple[dict[str, Any], uuid.UUID]],
    directories: list[tuple[dict[str, Any], uuid.UUID]],
) -> list[tuple[str, dict[str, Any], uuid.UUID]]:
    """Interleave independently ranked indexes without starving either kind.

    Meilisearch preserves relevance inside each index. Exact label matches are
    promoted to the front of their own list and determine which kind starts;
    otherwise the stronger normalized ranking score wins, with directories as
    the stable tie-breaker for direct navigation.
    """
    normalized_query = _normalized_label(query)

    def promote_exact(
        pairs: list[tuple[dict[str, Any], uuid.UUID]], label_key: str
    ) -> tuple[list[tuple[dict[str, Any], uuid.UUID]], bool]:
        exact_index = next(
            (
                index
                for index, (hit, _) in enumerate(pairs)
                if _normalized_label(hit.get(label_key)) == normalized_query
            ),
            None,
        )
        if exact_index is None:
            return pairs, False
        if exact_index == 0:
            return pairs, True
        return [pairs[exact_index], *pairs[:exact_index], *pairs[exact_index + 1 :]], True

    materials, material_exact = promote_exact(materials, "title")
    directories, directory_exact = promote_exact(directories, "name")

    if material_exact != directory_exact:
        next_kind = "material" if material_exact else "directory"
    else:
        material_score = float(materials[0][0].get("_rankingScore") or 0) if materials else -1
        directory_score = (
            float(directories[0][0].get("_rankingScore") or 0) if directories else -1
        )
        next_kind = "material" if material_score > directory_score else "directory"

    mixed: list[tuple[str, dict[str, Any], uuid.UUID]] = []
    material_index = 0
    directory_index = 0
    while material_index < len(materials) or directory_index < len(directories):
        if next_kind == "material" and material_index < len(materials):
            hit, parsed_id = materials[material_index]
            mixed.append(("material", hit, parsed_id))
            material_index += 1
            next_kind = "directory"
        elif next_kind == "directory" and directory_index < len(directories):
            hit, parsed_id = directories[directory_index]
            mixed.append(("directory", hit, parsed_id))
            directory_index += 1
            next_kind = "material"
        elif material_index < len(materials):
            hit, parsed_id = materials[material_index]
            mixed.append(("material", hit, parsed_id))
            material_index += 1
        else:
            hit, parsed_id = directories[directory_index]
            mixed.append(("directory", hit, parsed_id))
            directory_index += 1
    return mixed


def _match_explanation(query: str, hit: dict[str, Any], kind: str) -> tuple[str, str] | None:
    needle = _normalized_label(query)
    label_key = "name" if kind == "directory" else "title"
    if not needle or needle in _normalized_label(hit.get(label_key)):
        return None

    candidates: list[tuple[str, object]] = [
        ("file_name", hit.get("file_name")),
        ("tag", " · ".join(str(tag) for tag in (hit.get("tags") or []))),
        ("author", hit.get("authorName")),
        ("description", hit.get("description")),
        ("path", hit.get("ancestor_path")),
        ("code", hit.get("code")),
    ]
    for field, raw_value in candidates:
        value = " ".join(str(raw_value or "").split())
        if needle not in value.casefold():
            continue
        if field == "description" and len(value) > 160:
            match_index = value.casefold().find(needle)
            start = max(0, match_index - 55)
            end = min(len(value), match_index + len(needle) + 85)
            value = f"{'…' if start else ''}{value[start:end]}{'…' if end < len(value) else ''}"
        return field, value
    return None


async def _authoritative_search_hits(
    db: AsyncSession,
    query: str,
    material_filters: list[str],
    directory_filters: list[str],
    kind_filter: str | None = None,
) -> tuple[list[tuple[dict[str, Any], uuid.UUID]], list[tuple[dict[str, Any], uuid.UUID]]]:
    """Return all bounded live Meili hits, preserving per-index ranking order.

    Meilisearch remains the relevance engine, but PostgreSQL is the liveness
    authority. We deliberately filter before client pagination and count.
    """
    client = get_search_client()
    live_materials: list[tuple[dict[str, Any], uuid.UUID]] = []
    live_directories: list[tuple[dict[str, Any], uuid.UUID]] = []
    search_materials = kind_filter != "directory"
    search_directories = kind_filter != "material"
    scan_offset = 0

    while scan_offset < _SEARCH_SCAN_MAX_HITS_PER_INDEX:
        requests: list[SearchParams] = []
        if search_materials:
            requests.append(
                _search_params(
                    index_uid="materials",
                    query=query,
                    offset=scan_offset,
                    limit=_SEARCH_SCAN_BATCH,
                    filters=material_filters,
                )
            )
        if search_directories:
            requests.append(
                _search_params(
                    index_uid="directories",
                    query=query,
                    offset=scan_offset,
                    limit=_SEARCH_SCAN_BATCH,
                    filters=directory_filters,
                )
            )
        results = await client.multi_search(requests)
        result_index = 0
        materials_res = None
        directories_res = None
        if search_materials:
            materials_res = results[result_index]  # type: ignore[index]
            result_index += 1
        if search_directories:
            directories_res = results[result_index]  # type: ignore[index]

        material_hits = list(materials_res.hits) if materials_res else []
        directory_hits = list(directories_res.hits) if directories_res else []
        live_materials.extend(await _live_hits_for_batch(db, Material, material_hits))
        live_directories.extend(await _live_hits_for_batch(db, Directory, directory_hits))

        if len(material_hits) < _SEARCH_SCAN_BATCH and len(directory_hits) < _SEARCH_SCAN_BATCH:
            break
        scan_offset += _SEARCH_SCAN_BATCH

    return live_materials, live_directories


async def perform_search(
    db: AsyncSession,
    query: str,
    page: int = 1,
    limit: int = 10,
    current_user_id: uuid.UUID | None = None,
    directory_id: uuid.UUID | None = None,
    type_filter: str | None = None,
    kind_filter: str | None = None,
    material_type_filter: str | None = None,
    directory_type_filter: str | None = None,
    status_filter: str | None = None,
    recursive: bool = False,
) -> dict[str, Any]:
    if not query.strip():
        return {"items": [], "total": 0, "page": page, "limit": limit}

    offset = (page - 1) * limit

    material_filters: list[str] = []
    directory_filters: list[str] = []

    if directory_id is not None:
        scope_ids = (
            await _directory_scope_ids(db, directory_id) if recursive else {directory_id}
        )
        material_filters.append(_uuid_in_filter("directory_id", scope_ids))
        directory_filters.append(_uuid_in_filter("parent_id", scope_ids))

    if type_filter is not None:
        if not _SAFE_TYPE_RE.match(type_filter):
            return {"items": [], "total": 0, "page": page, "limit": limit}
        if type_filter == "directory":
            kind_filter = kind_filter or "directory"
        elif type_filter in {"folder", "module"}:
            kind_filter = kind_filter or "directory"
            directory_type_filter = directory_type_filter or type_filter
        else:
            kind_filter = kind_filter or "material"
            material_type_filter = material_type_filter or type_filter

    if material_type_filter is not None:
        if not _SAFE_TYPE_RE.match(material_type_filter):
            return {"items": [], "total": 0, "page": page, "limit": limit}
        material_filters.append(f'type = "{material_type_filter}"')
        if directory_type_filter is None:
            kind_filter = kind_filter or "material"

    if directory_type_filter is not None:
        if not _SAFE_TYPE_RE.match(directory_type_filter):
            return {"items": [], "total": 0, "page": page, "limit": limit}
        directory_filters.append(f'type = "{directory_type_filter}"')
        if material_type_filter is None:
            kind_filter = kind_filter or "directory"

    if status_filter is not None:
        if not _SAFE_TYPE_RE.match(status_filter):
            return {"items": [], "total": 0, "page": page, "limit": limit}
        material_filters.append(f'status = "{status_filter}"')
        directory_filters.append(f'status = "{status_filter}"')

    live_material_pairs, live_directory_pairs = await _authoritative_search_hits(
        db,
        query,
        material_filters,
        directory_filters,
        kind_filter,
    )
    if kind_filter == "material":
        live_directory_pairs = []
    elif kind_filter == "directory":
        live_material_pairs = []

    all_live_pairs = _mix_ranked_hits(query, live_material_pairs, live_directory_pairs)
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
        hit.pop("_rankingScore", None)
        hit.pop("_rankingScoreDetails", None)
        hit["search_type"] = kind
        explanation = _match_explanation(query, hit, kind)
        if explanation is not None:
            hit["matched_field"], hit["match_context"] = explanation
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
