"""Tests for the search service, router, rate limiting, and Meilisearch setup."""

import uuid
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
        email=f"{uuid.uuid4().hex[:8]}@telecom-sudparis.eu",
        display_name="Tester",
        role=role,
        onboarded=True,
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user


def _auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

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
    with patch("app.services.search.meili_search_client") as mock:
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
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())
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
    response = await client.get("/api/search?query=test&page=51", headers=_auth_headers(user))
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
async def test_search_success_materials_first(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """Materials come before directories (no client-side re-sort with Option B)."""
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
    assert data["items"][0]["search_type"] == "material"
    assert data["items"][0]["title"] == "Algebra Notes"
    assert data["items"][1]["search_type"] == "directory"
    assert data["items"][1]["name"] == "Mathematics"


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
    assert data["total"] == 5
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
    assert data["total"] == 3
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
async def test_search_total_sums_both_indexes(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """total = estimated_total_hits(materials) + estimated_total_hits(directories)."""
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(mat_total=17, dir_total=8)
    )
    response = await client.get("/api/search?query=test", headers=_auth_headers(user))
    assert response.json()["total"] == 25


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_pagination_offset(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """Correct offset and limit forwarded to Meilisearch (no *2 multiplier)."""
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response(mat_total=30))

    response = await client.get(
        "/api/search?query=test&page=3&limit=7", headers=_auth_headers(user)
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 3
    assert data["limit"] == 7

    params = mock_meili_client.multi_search.call_args[0][0]
    assert params[0].offset == 14  # (3-1)*7
    assert params[0].limit == 7
    assert params[1].offset == 14
    assert params[1].limit == 7


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
async def test_search_no_like_query_for_anonymous(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    """Anonymous search — no DB like queries executed."""
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(
        return_value=_meili_response(mat_hits=[{"id": str(uuid.uuid4()), "title": "Paper"}])
    )
    with patch("app.services.search.select") as mock_select:
        response = await client.get("/api/search?query=test")
        assert response.status_code == 200
        # select should not be called for likes when no user
        mock_select.assert_not_called()


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


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
    # directories index should NOT have directory_id filter
    assert params[1].filter is None


@pytest.mark.asyncio
async def test_search_filter_type_forwarded_to_both_indexes(
    client: AsyncClient, db_session: AsyncSession, mock_meili_client: AsyncMock
):
    user = await _create_user(db_session)
    await db_session.commit()
    mock_meili_client.multi_search = AsyncMock(return_value=_meili_response())

    response = await client.get("/api/search?query=test&type=document", headers=_auth_headers(user))
    assert response.status_code == 200

    params = mock_meili_client.multi_search.call_args[0][0]
    assert "document" in str(params[0].filter)
    assert "document" in str(params[1].filter)


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
    """setup_meilisearch skips update_settings when settings already match."""
    from meilisearch_python_sdk.models.settings import (
        MeilisearchSettings,
        MinWordSizeForTypos,
        TypoTolerance,
    )

    from app.core.meilisearch import _DIRECTORIES_RANKING_RULES, _MATERIALS_RANKING_RULES

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
        filterable_attributes=["type", "directory_id"],
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
        filterable_attributes=["parent_id", "type"],
        sortable_attributes=["like_count", "created_at"],
        ranking_rules=_DIRECTORIES_RANKING_RULES,
        typo_tolerance=TypoTolerance(
            enabled=True, min_word_size_for_typos=MinWordSizeForTypos(one_typo=5, two_typos=9)
        ),
    )

    def _index_side_effect(uid):
        mock_idx = AsyncMock()
        mock_idx.update_settings = AsyncMock(
            side_effect=lambda _: (_ for _ in ()).throw(
                AssertionError("update_settings called unexpectedly")
            )
        )
        mock_idx.get_settings = AsyncMock(
            return_value=desired_mat if uid == "materials" else desired_dir
        )
        return mock_idx

    mock_admin = MagicMock()
    mock_admin.get_indexes = AsyncMock(
        return_value=[MagicMock(uid="materials"), MagicMock(uid="directories")]
    )
    mock_admin.index = MagicMock(side_effect=_index_side_effect)

    with patch("app.core.meilisearch.meili_admin_client", mock_admin):
        from app.core.meilisearch import setup_meilisearch

        await setup_meilisearch()  # Must not raise


@pytest.mark.asyncio
async def test_settings_update_called_when_changed():
    """setup_meilisearch calls update_settings when ranking_rules differ."""
    from meilisearch_python_sdk.models.settings import MeilisearchSettings

    stale_settings = MeilisearchSettings(
        searchable_attributes=["title"],
        ranking_rules=["words", "typo"],  # missing like_count:desc etc.
    )
    update_called = []

    def _index_side_effect(uid):
        mock_idx = AsyncMock()
        mock_idx.get_settings = AsyncMock(return_value=stale_settings)
        mock_idx.update_settings = AsyncMock(side_effect=lambda _: update_called.append(uid))
        return mock_idx

    mock_admin = MagicMock()
    mock_admin.get_indexes = AsyncMock(
        return_value=[MagicMock(uid="materials"), MagicMock(uid="directories")]
    )
    mock_admin.index = MagicMock(side_effect=_index_side_effect)

    with patch("app.core.meilisearch.meili_admin_client", mock_admin):
        from app.core.meilisearch import setup_meilisearch

        await setup_meilisearch()

    assert "materials" in update_called
    assert "directories" in update_called


# ---------------------------------------------------------------------------
# Search client isolation
# ---------------------------------------------------------------------------


def test_search_client_uses_search_key_when_configured():
    """meili_search_client is a separate object when MEILI_SEARCH_KEY is set."""
    import importlib
    from unittest.mock import patch

    import app.core.meilisearch as meili_mod

    with patch("app.config.settings") as mock_settings:
        mock_settings.meili_url = "http://localhost:7700"
        mock_settings.meili_master_key = "master-key"
        mock_settings.meili_search_key = "search-only-key"
        mock_settings.is_dev = False

        # Reload the module to trigger the client creation logic with patched settings
        importlib.reload(meili_mod)

        assert meili_mod.meili_search_client is not meili_mod.meili_admin_client

    # Restore original state
    importlib.reload(meili_mod)


def test_search_client_falls_back_to_admin_when_no_key():
    """When MEILI_SEARCH_KEY is unset, meili_search_client is meili_admin_client."""
    import importlib
    from unittest.mock import patch

    import app.core.meilisearch as meili_mod

    with patch("app.config.settings") as mock_settings:
        mock_settings.meili_url = "http://localhost:7700"
        mock_settings.meili_master_key = "master-key"
        mock_settings.meili_search_key = None
        mock_settings.is_dev = True

        importlib.reload(meili_mod)
        assert meili_mod.meili_search_client is meili_mod.meili_admin_client


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_search_anonymous_enforced(mock_redis):
    """Anonymous users are blocked after 30 requests (prod) / pass in dev."""
    from app.config import settings
    from app.core.exceptions import RateLimitError
    from app.dependencies.rate_limit import rate_limit_search

    if settings.is_dev:
        pytest.skip("Rate limit disabled in dev")

    request = MagicMock()
    request.client.host = "1.2.3.4"

    # Simulate counter already at 31
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)
    pipe.incr = AsyncMock(return_value=pipe)
    pipe.expire = AsyncMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[31, True])
    mock_redis.pipeline = MagicMock(return_value=pipe)

    with pytest.raises(RateLimitError):
        await rate_limit_search(request=request, redis=mock_redis, user=None)


@pytest.mark.asyncio
async def test_rate_limit_search_authenticated_higher_limit(mock_redis):
    """Authenticated users have 120/min limit."""
    from app.config import settings
    from app.dependencies.rate_limit import rate_limit_search

    if settings.is_dev:
        pytest.skip("Rate limit disabled in dev")

    user = MagicMock()
    user.id = uuid.uuid4()

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
    from app.core.exceptions import RateLimitError
    from app.dependencies.rate_limit import rate_limit_search

    if settings.is_dev:
        pytest.skip("Rate limit disabled in dev")

    user = MagicMock()
    user.id = uuid.uuid4()

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
