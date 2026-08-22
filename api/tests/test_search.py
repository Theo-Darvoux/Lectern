"""Tests for the search service, router, rate limiting, and Meilisearch setup."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_user(db: AsyncSession, role: UserRole = UserRole.STUDENT) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security.security import create_access_token

    token, _ = create_access_token(str(user.id), user.role.value, user.email)
    return {"Authorization": f"Bearer {token}"}


class _MockHits:
    def __init__(self, hits, est_total=None):
        self.hits = hits
        self.estimated_total_hits = est_total if est_total is not None else len(hits)


def _meili_response(mat_hits=None, dir_hits=None, mat_total=None, dir_total=None):
    mat_hits = mat_hits or []
    dir_hits = dir_hits or []
    return [
        _MockHits(mat_hits, mat_total if mat_total is not None else len(mat_hits)),
        _MockHits(dir_hits, dir_total if dir_total is not None else len(dir_hits)),
    ]


@pytest.fixture
def mock_meili_client():
    # Most tests in this module exercise query shaping/formatting with synthetic
    # Meili UUIDs that are intentionally not persisted. Keep those concerns
    # isolated; dedicated durability tests exercise the real PostgreSQL liveness
    # filter against live/deleted/missing rows.
    with (
        patch("app.services.search.get_search_client") as get_client,
        patch("app.services.search._authoritative_live_ids", new_callable=AsyncMock) as live_ids,
    ):
        mock = AsyncMock()
        get_client.return_value = mock
        live_ids.side_effect = lambda _db, _model, ids: set(ids)
        yield mock


# ---------------------------------------------------------------------------
# Router validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_empty_query_rejected(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """Empty query string → 422."""
    user = await _create_user(db_session)
    await db_session.commit()
    response = await client.get("/api/search?query=", headers=_auth_headers(user))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_missing_query_rejected(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """Missing query param → 422."""
    user = await _create_user(db_session)
    await db_session.commit()
    response = await client.get("/api/search", headers=_auth_headers(user))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_query_too_long(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """201-char query → 422."""
    user = await _create_user(db_session)
    await db_session.commit()
    response = await client.get(f"/api/search?query={'a' * 201}", headers=_auth_headers(user))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_query_max_length_accepted(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """200-char query → accepted."""
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            mat_hits=[{"id": str(uuid.uuid4()), "title": "Document"}],
            dir_hits=[{"id": str(uuid.uuid4()), "name": "Document folder"}],
        )
    )
    response = await client.get(f"/api/search?query={'a' * 200}", headers=_auth_headers(user))
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_page_zero_rejected(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    response = await client.get("/api/search?query=test&page=0", headers=_auth_headers(user))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_page_above_cap_rejected(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    response = await client.get("/api/search?query=test&page=2001", headers=_auth_headers(user))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_limit_above_cap_rejected(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    response = await client.get("/api/search?query=test&limit=51", headers=_auth_headers(user))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_limit_50_accepted(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())
    response = await client.get("/api/search?query=test&limit=50", headers=_auth_headers(user))
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters",
    [
        "kind=directory&material_type=document",
        "kind=material&directory_type=folder",
    ],
)
async def test_search_rejects_conflicting_kind_and_subtype_filters(
    filters: str,
    client: AsyncClient,
    db_session: AsyncSession,
    mock_meili_client: AsyncMock,
):
    user = await _create_user(db_session)
    await db_session.commit()

    response = await client.get(
        f"/api/search?query=test&{filters}", headers=_auth_headers(user)
    )

    assert response.status_code == 400
    mock_meili_client.multi_search.assert_not_awaited()


# ---------------------------------------------------------------------------
# Service-level guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_whitespace_query_returns_empty(mock_meili_client: AsyncMock):
    """Service-level guard returns empty without hitting Meili."""
    from app.services.search import perform_search

    db = MagicMock()
    result = await perform_search(db, "   ")
    assert result == {"items": [], "total": 0, "page": 1, "limit": 10}
    mock_meili_client.multi_search.assert_not_called()


@pytest.mark.asyncio
async def test_service_empty_string_returns_empty(mock_meili_client: AsyncMock):
    from app.services.search import perform_search

    db = MagicMock()
    result = await perform_search(db, "")
    assert result == {"items": [], "total": 0, "page": 1, "limit": 10}
    mock_meili_client.multi_search.assert_not_called()


# ---------------------------------------------------------------------------
# Successful search — basic structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_success_directories_first(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """Directories come before materials so container navigation is prioritized."""
    user = await _create_user(db_session)
    await db_session.commit()

    mat_id, dir_id = str(uuid.uuid4()), str(uuid.uuid4())
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            mat_hits=[{"id": mat_id, "title": "Algebra Notes"}],
            dir_hits=[{"id": dir_id, "name": "Mathematics"}],
        )
    )

    response = await client.get("/api/search?query=algebra", headers=_auth_headers(user))
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["search_type"] == "directory"
    assert data["items"][0]["name"] == "Mathematics"
    assert data["items"][1]["search_type"] == "material"
    assert data["items"][1]["title"] == "Algebra Notes"


@pytest.mark.asyncio
async def test_search_mixes_entity_kinds_and_promotes_exact_name_match(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    exact_material_id = str(uuid.uuid4())
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            mat_hits=[
                {"id": exact_material_id, "title": "Algebra"},
                {"id": str(uuid.uuid4()), "title": "Algebra exercises"},
            ],
            dir_hits=[
                {"id": str(uuid.uuid4()), "name": "Algebra archive"},
                {"id": str(uuid.uuid4()), "name": "Algebra module"},
            ],
        )
    )

    response = await client.get("/api/search?query=algebra", headers=_auth_headers(user))

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["id"] == exact_material_id
    assert [item["search_type"] for item in items] == [
        "material",
        "directory",
        "material",
        "directory",
    ]


@pytest.mark.asyncio
async def test_search_materials_only(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            mat_hits=[{"id": str(uuid.uuid4()), "title": "Physics"}], mat_total=5
        )
    )
    response = await client.get("/api/search?query=physics", headers=_auth_headers(user))
    data = response.json()
    # Client-facing total is computed from PostgreSQL-validated hits, never the
    # stale/approximate Meilisearch estimate.
    assert data["total"] == 1
    assert all(i["search_type"] == "material" for i in data["items"])


@pytest.mark.asyncio
async def test_search_directories_only(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            dir_hits=[{"id": str(uuid.uuid4()), "name": "CS Dept"}], dir_total=3
        )
    )
    response = await client.get("/api/search?query=cs", headers=_auth_headers(user))
    data = response.json()
    assert data["total"] == 1
    assert all(i["search_type"] == "directory" for i in data["items"])


@pytest.mark.asyncio
async def test_search_empty_results(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())
    response = await client.get("/api/search?query=xyzzy", headers=_auth_headers(user))
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_search_result_explains_non_title_match(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            mat_hits=[
                {
                    "id": str(uuid.uuid4()),
                    "title": "Week 4 notes",
                    "description": "Worked examples for linear algebra and matrices.",
                }
            ]
        )
    )

    response = await client.get("/api/search?query=algebra", headers=_auth_headers(user))

    item = response.json()["items"][0]
    assert item["matched_field"] == "description"
    assert "linear algebra" in item["match_context"].lower()


@pytest.mark.asyncio
async def test_search_total_ignores_meili_estimate(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """total reflects validated live hits, not Meilisearch's stale estimate."""
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            mat_hits=[{"id": str(uuid.uuid4()), "title": "One"}],
            dir_hits=[{"id": str(uuid.uuid4()), "name": "Two"}],
            mat_total=17,
            dir_total=8,
        )
    )
    response = await client.get("/api/search?query=test", headers=_auth_headers(user))
    assert response.json()["total"] == 2


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_pagination_happens_after_authoritative_scan(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """Meili is scanned from zero; client page slicing happens after PG validation."""
    user = await _create_user(db_session)
    await db_session.commit()
    hits = [{"id": str(uuid.uuid4()), "title": f"Material {i}"} for i in range(20)]
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(mat_hits=hits, mat_total=20)
    )

    response = await client.get(
        "/api/search?query=test&page=3&limit=7", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 3
    assert data["limit"] == 7
    assert data["total"] == 20
    assert [item["title"] for item in data["items"]] == [f"Material {i}" for i in range(14, 20)]

    params = mock_meili_client.multi_search.call_args[0][0]
    assert params[0].offset == 0
    assert params[0].limit == 250
    assert params[1].offset == 0
    assert params[1].limit == 250


@pytest.mark.asyncio
async def test_search_page1_offset_zero(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())

    await client.get("/api/search?query=test&page=1&limit=10", headers=_auth_headers(user))
    params = mock_meili_client.multi_search.call_args[0][0]
    assert params[0].offset == 0


# ---------------------------------------------------------------------------
# is_liked field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_is_liked_set_for_liked_material(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    from app.models.material import Material, MaterialLike

    user = await _create_user(db_session)

    mat = Material(
        id=uuid.uuid4(),
        title="Liked Paper",
        slug="liked-paper",
        type="document",
        author_id=user.id,
        tags=[],
    )
    db_session.add(mat)
    await db_session.flush()

    like = MaterialLike(id=uuid.uuid4(), user_id=user.id, material_id=mat.id)
    db_session.add(like)
    await db_session.commit()

    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(mat_hits=[{"id": str(mat.id), "title": "Liked Paper"}])
    )
    response = await client.get("/api/search?query=liked", headers=_auth_headers(user))
    data = response.json()
    assert data["items"][0]["is_liked"] is True


@pytest.mark.asyncio
async def test_search_is_liked_false_for_other_material(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()

    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(mat_hits=[{"id": str(uuid.uuid4()), "title": "Not Liked"}])
    )
    response = await client.get("/api/search?query=test", headers=_auth_headers(user))
    assert response.json()["items"][0]["is_liked"] is False


@pytest.mark.asyncio
async def test_search_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """Unauthenticated search is rejected with 401 Unauthorized."""
    response = await client.get("/api/search?query=test")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_type"),
    [("material", "material"), ("directory", "directory")],
)
async def test_search_kind_returns_only_requested_entity_kind(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_meili_client: AsyncMock,
    kind: str,
    expected_type: str,
):
    user = await _create_user(db_session)
    await db_session.commit()
    material_response = _meili_response(
        mat_hits=[{"id": str(uuid.uuid4()), "title": "Shared result"}]
    )[:1]
    directory_response = _meili_response(
        dir_hits=[{"id": str(uuid.uuid4()), "name": "Shared result"}]
    )[1:]
    mock_meili_client.multi_search = AsyncMock(
        return_value=material_response if kind == "material" else directory_response
    )

    response = await client.get(
        f"/api/search?query=shared&kind={kind}", headers=_auth_headers(user)
    )

    assert response.status_code == 200
    assert {item["search_type"] for item in response.json()["items"]} == {expected_type}
    params = mock_meili_client.multi_search.call_args.args[0]
    assert [param.index_uid for param in params] == [
        "materials" if kind == "material" else "directories"
    ]


@pytest.mark.asyncio
async def test_search_filter_directory_id_forwarded(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    dir_id = uuid.uuid4()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())

    response = await client.get(
        f"/api/search?query=test&directory_id={dir_id}", headers=_auth_headers(user)
    )
    assert response.status_code == 200

    params = mock_meili_client.multi_search.call_args[0][0]
    mat_filter = params[0].filter
    assert mat_filter is not None
    assert str(dir_id) in str(mat_filter)
    # Directory scope searches immediate child folders as well as materials.
    assert str(dir_id) in str(params[1].filter)


@pytest.mark.asyncio
async def test_search_recursive_directory_scope_includes_nested_descendants(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    from app.models.directory import Directory, DirectoryType

    user = await _create_user(db_session)
    root = Directory(name="Root", slug=f"root-{uuid.uuid4().hex}", type=DirectoryType.MODULE)
    child = Directory(
        name="Child",
        slug=f"child-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
        parent=root,
    )
    grandchild = Directory(
        name="Grandchild",
        slug=f"grandchild-{uuid.uuid4().hex}",
        type=DirectoryType.FOLDER,
        parent=child,
    )
    sibling = Directory(
        name="Outside",
        slug=f"outside-{uuid.uuid4().hex}",
        type=DirectoryType.MODULE,
    )
    db_session.add_all([root, child, grandchild, sibling])
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())

    response = await client.get(
        f"/api/search?query=test&directory_id={root.id}&recursive=true",
        headers=_auth_headers(user),
    )

    assert response.status_code == 200
    material_filter = str(mock_meili_client.multi_search.call_args[0][0][0].filter)
    directory_filter = str(mock_meili_client.multi_search.call_args[0][0][1].filter)
    for directory_id in (root.id, child.id, grandchild.id):
        assert str(directory_id) in material_filter
        assert str(directory_id) in directory_filter
    assert str(sibling.id) not in material_filter
    assert str(sibling.id) not in directory_filter


@pytest.mark.asyncio
async def test_search_legacy_material_type_is_not_applied_to_directory_index(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())

    response = await client.get("/api/search?query=test&type=document", headers=_auth_headers(user))
    assert response.status_code == 200

    params = mock_meili_client.multi_search.call_args[0][0]
    assert len(params) == 1
    assert "document" in str(params[0].filter)


@pytest.mark.asyncio
async def test_search_material_type_and_status_filters_target_the_correct_indexes(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            mat_hits=[{"id": str(uuid.uuid4()), "title": "Document"}],
        )[:1]
    )

    response = await client.get(
        "/api/search?query=test&material_type=document&status=current",
        headers=_auth_headers(user),
    )

    assert response.status_code == 200
    params = mock_meili_client.multi_search.call_args[0][0]
    assert len(params) == 1
    assert "document" in str(params[0].filter)
    assert "current" in str(params[0].filter)
    assert [item["search_type"] for item in response.json()["items"]] == ["material"]


@pytest.mark.asyncio
async def test_search_legacy_directory_type_returns_directories(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(
            dir_hits=[{"id": str(uuid.uuid4()), "name": "Folder"}],
        )[1:]
    )

    response = await client.get(
        "/api/search?query=test&type=directory", headers=_auth_headers(user)
    )

    assert response.status_code == 200
    assert [item["search_type"] for item in response.json()["items"]] == ["directory"]


@pytest.mark.asyncio
async def test_search_filter_type_and_directory_combined(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    dir_id = uuid.uuid4()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())

    response = await client.get(
        f"/api/search?query=test&directory_id={dir_id}&type=document",
        headers=_auth_headers(user),
    )
    assert response.status_code == 200
    params = mock_meili_client.multi_search.call_args[0][0]
    mat_filter = str(params[0].filter)
    assert str(dir_id) in mat_filter
    assert "document" in mat_filter


# ---------------------------------------------------------------------------
# Filter injection / safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_filter_type_injection_blocked(mock_meili_client: AsyncMock):
    """Malicious type values are rejected before hitting Meili."""
    from app.services.search import perform_search

    db = MagicMock()
    result = await perform_search(db, "test", type_filter="' OR 1=1 --")
    assert result["items"] == []
    assert result["total"] == 0
    mock_meili_client.multi_search.assert_not_called()


@pytest.mark.asyncio
async def test_service_filter_type_injection_semicolon(mock_meili_client: AsyncMock):
    from app.services.search import perform_search

    db = MagicMock()
    result = await perform_search(db, "test", type_filter="pdf; DROP TABLE materials")
    assert result["total"] == 0
    mock_meili_client.multi_search.assert_not_called()


@pytest.mark.asyncio
async def test_service_filter_type_valid_values_allowed(mock_meili_client: AsyncMock):
    """Valid type strings pass the allowlist and reach Meili."""
    from app.services.search import perform_search

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))

    for valid_type in ("document", "video", "polycopie", "module", "CS101", "other"):
        mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())
        await perform_search(db, "test", type_filter=valid_type)
        mock_meili_client.multi_search.assert_called_once()
        mock_meili_client.reset_mock()


# ---------------------------------------------------------------------------
# Meilisearch settings idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_idempotency_no_update_when_unchanged():
    """setup_meilisearch skips settings/pagination updates when already aligned."""
    from meilisearch_python_sdk.models.settings import (
        MeilisearchSettings,
        MinWordSizeForTypos,
        Pagination,
        TypoTolerance,
    )

    from app.core.events.meilisearch import (
        _DIRECTORIES_RANKING_RULES,
        _MATERIALS_RANKING_RULES,
        SEARCH_MAX_TOTAL_HITS,
    )

    desired_mat = MeilisearchSettings(
        searchable_attributes=[
            "title",
            "description",
            "tags",
            "slug",
            "type",
            "authorName",
            "ancestor_path",
            "extra_searchable",
        ],
        filterable_attributes=["type", "directory_id", "status"],
        sortable_attributes=["like_count", "total_views", "created_at"],
        ranking_rules=_MATERIALS_RANKING_RULES,
        typo_tolerance=TypoTolerance(
            enabled=True, min_word_size_for_typos=MinWordSizeForTypos(one_typo=5, two_typos=9)
        ),
    )
    desired_dir = MeilisearchSettings(
        searchable_attributes=[
            "name",
            "description",
            "slug",
            "type",
            "tags",
            "code",
            "ancestor_path",
            "extra_searchable",
        ],
        filterable_attributes=["parent_id", "type", "status"],
        sortable_attributes=["like_count", "created_at"],
        ranking_rules=_DIRECTORIES_RANKING_RULES,
        typo_tolerance=TypoTolerance(
            enabled=True, min_word_size_for_typos=MinWordSizeForTypos(one_typo=5, two_typos=9)
        ),
    )

    indexes: dict[str, AsyncMock] = {}
    for uid, desired in (("materials", desired_mat), ("directories", desired_dir)):
        mock_idx = AsyncMock()
        mock_idx.get_settings = AsyncMock(return_value=desired)
        mock_idx.update_settings = AsyncMock(
            side_effect=AssertionError("update_settings called unexpectedly")
        )
        mock_idx.get_pagination = AsyncMock(
            return_value=Pagination(max_total_hits=SEARCH_MAX_TOTAL_HITS)
        )
        mock_idx.update_pagination = AsyncMock(
            side_effect=AssertionError("update_pagination called unexpectedly")
        )
        indexes[uid] = mock_idx

    mock_admin = MagicMock()
    mock_admin.get_indexes = AsyncMock(
        return_value=[MagicMock(uid="materials"), MagicMock(uid="directories")]
    )
    mock_admin.index = MagicMock(side_effect=lambda uid: indexes[uid])
    mock_admin.wait_for_task = AsyncMock()

    with (
        patch("app.core.events.meilisearch.meili_admin_client", mock_admin),
        patch("app.core.events.meilisearch._ensure_search_key", AsyncMock(return_value="test-key")),
        patch("app.core.events.meilisearch.AsyncClient"),
    ):
        from app.core.events.meilisearch import setup_meilisearch

        await setup_meilisearch()

    mock_admin.wait_for_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_update_called_when_changed():
    """setup_meilisearch calls update_settings when ranking_rules differ."""
    from meilisearch_python_sdk.models.settings import MeilisearchSettings, Pagination

    from app.core.events.meilisearch import SEARCH_MAX_TOTAL_HITS

    stale_settings = MeilisearchSettings(
        searchable_attributes=["title"],
        ranking_rules=["words", "typo"],  # missing like_count:desc etc.
    )
    update_called: list[str] = []
    indexes: dict[str, AsyncMock] = {}

    for uid in ("materials", "directories"):
        mock_idx = AsyncMock()
        mock_idx.get_settings = AsyncMock(return_value=stale_settings)
        mock_idx.update_settings = AsyncMock(
            side_effect=lambda _settings, uid=uid: (
                update_called.append(uid),
                SimpleNamespace(task_uid=801 if uid == "materials" else 802),
            )[1]
        )
        mock_idx.get_pagination = AsyncMock(
            return_value=Pagination(max_total_hits=SEARCH_MAX_TOTAL_HITS)
        )
        mock_idx.update_pagination = AsyncMock(
            side_effect=AssertionError("pagination was already aligned")
        )
        indexes[uid] = mock_idx

    mock_admin = MagicMock()
    mock_admin.get_indexes = AsyncMock(
        return_value=[MagicMock(uid="materials"), MagicMock(uid="directories")]
    )
    mock_admin.index = MagicMock(side_effect=lambda uid: indexes[uid])
    mock_admin.wait_for_task = AsyncMock()

    with (
        patch("app.core.events.meilisearch.meili_admin_client", mock_admin),
        patch("app.core.events.meilisearch._ensure_search_key", AsyncMock(return_value="test-key")),
        patch("app.core.events.meilisearch.AsyncClient"),
    ):
        from app.core.events.meilisearch import setup_meilisearch

        await setup_meilisearch()

    assert update_called == ["materials", "directories"]
    assert mock_admin.wait_for_task.await_count == 2


@pytest.mark.asyncio
async def test_pagination_limit_is_explicitly_repaired_and_waited():
    """The authoritative scan bound and Meilisearch maxTotalHits cannot drift."""
    from meilisearch_python_sdk.models.settings import Pagination

    from app.core.events.meilisearch import (
        SEARCH_MAX_TOTAL_HITS,
        _apply_pagination_if_changed,
    )

    index = AsyncMock()
    index.get_pagination = AsyncMock(return_value=Pagination(max_total_hits=5_000))
    index.update_pagination = AsyncMock(return_value=SimpleNamespace(task_uid=901))
    admin = MagicMock()
    admin.index = MagicMock(return_value=index)
    admin.wait_for_task = AsyncMock(return_value=SimpleNamespace(status="succeeded"))

    with patch("app.core.events.meilisearch.meili_admin_client", admin):
        await _apply_pagination_if_changed("materials")

    desired = index.update_pagination.await_args.args[0]
    assert desired.max_total_hits == SEARCH_MAX_TOTAL_HITS == 1_000
    admin.wait_for_task.assert_awaited_once_with(
        901,
        timeout_in_ms=30_000,
        raise_for_status=True,
    )


# ---------------------------------------------------------------------------
# Search client isolation
# ---------------------------------------------------------------------------


def test_search_client_starts_as_none_at_module_load():
    """At module load, meili_search_client is None to prevent privilege escalation."""

    import importlib

    import app.core.events.meilisearch as meili_mod

    importlib.reload(meili_mod)

    # 1. Verify it strictly defaults to None
    assert meili_mod.meili_search_client is None

    # 2. Verify the safe accessor blocks premature execution
    with pytest.raises(RuntimeError, match="accessed before initialization"):
        meili_mod.get_search_client()

    # Restore the module state for downstream tests
    importlib.reload(meili_mod)


@pytest.mark.asyncio
async def test_search_client_replaced_after_setup_meilisearch():
    """After setup_meilisearch(), meili_search_client is a distinct search-only client."""
    import app.core.events.meilisearch as ms_module
    from app.core.events.meilisearch import setup_meilisearch

    original_admin = ms_module.meili_admin_client
    new_search_client = MagicMock()

    mock_admin = AsyncMock()
    mock_admin.get_indexes = AsyncMock(return_value=[])
    mock_admin.index = MagicMock(
        return_value=MagicMock(
            get_settings=AsyncMock(side_effect=Exception("no settings")),
            update_settings=AsyncMock(return_value=SimpleNamespace(task_uid=701)),
            get_pagination=AsyncMock(
                return_value=MagicMock(max_total_hits=ms_module.SEARCH_MAX_TOTAL_HITS)
            ),
            update_pagination=AsyncMock(),
        )
    )

    with (
        patch.object(ms_module, "meili_admin_client", mock_admin),
        patch.object(ms_module, "_ensure_search_key", AsyncMock(return_value="auto-key")),
        patch("app.core.events.meilisearch.AsyncClient", return_value=new_search_client),
    ):
        await setup_meilisearch()
        assert ms_module.meili_search_client is new_search_client
        assert ms_module.meili_search_client is not original_admin


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_search_authenticated_higher_limit(mock_redis):
    """Authenticated users have 120/min limit."""
    from app.config import settings
    from app.dependencies.rate_limit import rate_limit_search

    if settings.is_dev:
        pytest.skip("Rate limit disabled in dev")

    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = UserRole.STUDENT

    request = MagicMock()
    request.client.host = "1.2.3.4"

    # 31 requests — OK for authed (limit is 120)
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)
    pipe.incr = AsyncMock(return_value=pipe)
    pipe.expire = AsyncMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[31, True])
    mock_redis.pipeline = MagicMock(return_value=pipe)

    # Should not raise for count=31 with auth
    await rate_limit_search(request=request, redis=mock_redis, user=user)


@pytest.mark.asyncio
async def test_rate_limit_search_authenticated_blocked_at_121(mock_redis):
    """Authenticated users blocked at 121/min."""
    from app.config import settings
    from app.core.common.exceptions import RateLimitError
    from app.dependencies.rate_limit import rate_limit_search

    if settings.is_dev:
        pytest.skip("Rate limit disabled in dev")

    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = UserRole.STUDENT

    request = MagicMock()
    request.client.host = "1.2.3.4"

    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)
    pipe.incr = AsyncMock(return_value=pipe)
    pipe.expire = AsyncMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[121, True])
    mock_redis.pipeline = MagicMock(return_value=pipe)

    with pytest.raises(RateLimitError):
        await rate_limit_search(request=request, redis=mock_redis, user=user)


# ---------------------------------------------------------------------------
# _ensure_search_key — auto-provisioning
# ---------------------------------------------------------------------------


def _make_key(name: str, key_value: str) -> MagicMock:
    k = MagicMock()
    k.name = name
    k.key = key_value
    return k


def _make_keys_result(keys: list) -> MagicMock:
    r = MagicMock()
    r.results = keys
    return r


@pytest.mark.asyncio
async def test_ensure_search_key_uses_valid_env_key():
    """If MEILI_SEARCH_KEY is set and valid, _ensure_search_key returns it as-is."""
    import app.core.events.meilisearch as ms_module
    from app.core.events.meilisearch import _ensure_search_key

    probe = AsyncMock()
    probe.__aenter__ = AsyncMock(return_value=probe)
    probe.__aexit__ = AsyncMock(return_value=None)
    probe.index = MagicMock(return_value=MagicMock(search=AsyncMock(return_value={})))

    with (
        patch.object(ms_module.settings, "meili_search_key", "valid-key-abc"),
        patch("app.core.events.meilisearch.AsyncClient", return_value=probe),
    ):
        result = await _ensure_search_key()

    assert result == "valid-key-abc"


@pytest.mark.asyncio
async def test_ensure_search_key_reuses_existing_provisioned_key():
    """If the env key is invalid but a named key already exists, reuse it."""
    from meilisearch_python_sdk.errors import MeilisearchApiError

    import app.core.events.meilisearch as ms_module
    from app.core.events.meilisearch import _SEARCH_KEY_NAME, _ensure_search_key

    # Probe client raises 403
    probe = AsyncMock()
    probe.__aenter__ = AsyncMock(return_value=probe)
    probe.__aexit__ = AsyncMock(return_value=None)
    probe.index = MagicMock(
        return_value=MagicMock(
            search=AsyncMock(
                side_effect=MeilisearchApiError("invalid_api_key", MagicMock(status_code=403))
            )
        )
    )

    existing_key = _make_key(_SEARCH_KEY_NAME, "existing-search-key")
    mock_admin = AsyncMock()
    mock_admin.get_keys = AsyncMock(return_value=_make_keys_result([existing_key]))

    with (
        patch.object(ms_module.settings, "meili_search_key", "stale-key"),
        patch("app.core.events.meilisearch.AsyncClient", return_value=probe),
        patch.object(ms_module, "meili_admin_client", mock_admin),
    ):
        result = await _ensure_search_key()

    assert result == "existing-search-key"
    mock_admin.create_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_search_key_creates_key_when_none_exists():
    """If no valid key exists, _ensure_search_key creates and returns a new one."""
    import app.core.events.meilisearch as ms_module
    from app.core.events.meilisearch import _ensure_search_key

    new_key = _make_key("lectern-search-key", "brand-new-key")

    mock_admin = AsyncMock()
    mock_admin.get_keys = AsyncMock(return_value=_make_keys_result([]))
    mock_admin.create_key = AsyncMock(return_value=new_key)

    with (
        patch.object(ms_module.settings, "meili_search_key", None),
        patch.object(ms_module, "meili_admin_client", mock_admin),
    ):
        result = await _ensure_search_key()

    assert result == "brand-new-key"
    mock_admin.create_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_meilisearch_updates_search_client():
    """setup_meilisearch must replace meili_search_client with a valid-key client."""
    import app.core.events.meilisearch as ms_module
    from app.core.events.meilisearch import setup_meilisearch

    mock_admin = AsyncMock()
    mock_admin.get_indexes = AsyncMock(return_value=[])
    mock_admin.index = MagicMock(
        return_value=MagicMock(
            get_settings=AsyncMock(side_effect=Exception("no settings")),
            update_settings=AsyncMock(return_value=SimpleNamespace(task_uid=702)),
            get_pagination=AsyncMock(
                return_value=MagicMock(max_total_hits=ms_module.SEARCH_MAX_TOTAL_HITS)
            ),
            update_pagination=AsyncMock(),
        )
    )

    new_client = MagicMock()
    with (
        patch.object(ms_module, "meili_admin_client", mock_admin),
        patch.object(ms_module, "_ensure_search_key", AsyncMock(return_value="auto-key")),
        patch("app.core.events.meilisearch.AsyncClient", return_value=new_client),
    ):
        await setup_meilisearch()

    assert ms_module.meili_search_client is new_client
