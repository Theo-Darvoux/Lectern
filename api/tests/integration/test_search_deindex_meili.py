from __future__ import annotations

import contextlib
import os
import uuid
from unittest.mock import patch

import pytest
from meilisearch_python_sdk import AsyncClient

from app.workers.index_content import delete_indexed_item

MEILI_URL = os.environ.get("SEARCH_DEINDEX_MEILI_URL")
MEILI_MASTER_KEY = os.environ.get("SEARCH_DEINDEX_MEILI_MASTER_KEY")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not MEILI_URL,
        reason="SEARCH_DEINDEX_MEILI_URL is not configured",
    ),
]


@pytest.mark.asyncio
async def test_real_meili_delete_waits_until_document_is_absent() -> None:
    assert MEILI_URL is not None
    client = AsyncClient(MEILI_URL, MEILI_MASTER_KEY)
    index = client.index("materials")
    document_id = str(uuid.uuid4())
    marker = f"deindex-real-{uuid.uuid4().hex}"

    try:
        add_task = await index.add_documents([{"id": document_id, "title": marker}])
        await client.wait_for_task(
            add_task.task_uid,
            timeout_in_ms=30_000,
            raise_for_status=True,
        )

        with patch("app.workers.index_content.meili_admin_client", client):
            await delete_indexed_item({}, "materials", document_id)

        result = await index.search(marker)
        assert all(str(hit.get("id")) != document_id for hit in result.hits)
    finally:
        # Idempotent cleanup for a failed assertion/setup path.
        with contextlib.suppress(Exception):
            cleanup_task = await index.delete_document(document_id)
            await client.wait_for_task(
                cleanup_task.task_uid,
                timeout_in_ms=30_000,
                raise_for_status=True,
            )
        await client.aclose()


@pytest.mark.asyncio
async def test_real_meili_pagination_horizon_matches_authoritative_scan() -> None:
    from app.core.events.meilisearch import (
        SEARCH_MAX_TOTAL_HITS,
        _apply_pagination_if_changed,
    )

    assert MEILI_URL is not None
    client = AsyncClient(MEILI_URL, MEILI_MASTER_KEY)
    index = client.index("materials")
    marker_id = str(uuid.uuid4())
    try:
        # Ensure the index exists even when this test is selected independently.
        add_task = await index.add_documents([{"id": marker_id, "title": "pagination-probe"}])
        await client.wait_for_task(
            add_task.task_uid,
            timeout_in_ms=30_000,
            raise_for_status=True,
        )

        with patch("app.core.events.meilisearch.meili_admin_client", client):
            await _apply_pagination_if_changed("materials")

        pagination = await index.get_pagination()
        assert pagination.max_total_hits == SEARCH_MAX_TOTAL_HITS == 1_000
    finally:
        with contextlib.suppress(Exception):
            cleanup_task = await index.delete_document(marker_id)
            await client.wait_for_task(
                cleanup_task.task_uid,
                timeout_in_ms=30_000,
                raise_for_status=True,
            )
        await client.aclose()


@pytest.mark.asyncio
async def test_real_meili_normal_update_waits_until_new_fields_are_searchable() -> None:
    """Exercise the exact normal-update helper used by index workers against real Meili."""
    from app.workers.index_content import _submit_index_documents

    assert MEILI_URL is not None
    client = AsyncClient(MEILI_URL, MEILI_MASTER_KEY)
    index = client.index("materials")
    document_id = str(uuid.uuid4())
    old_marker = f"normal-update-old-{uuid.uuid4().hex}"
    new_marker = f"normal-update-new-{uuid.uuid4().hex}"

    try:
        with patch("app.workers.index_content.meili_admin_client", client):
            await _submit_index_documents("materials", [{"id": document_id, "title": old_marker}])
            await _submit_index_documents("materials", [{"id": document_id, "title": new_marker}])

        new_result = await index.search(new_marker)
        assert any(str(hit.get("id")) == document_id for hit in new_result.hits)
        old_result = await index.search(old_marker)
        assert all(str(hit.get("id")) != document_id for hit in old_result.hits)
    finally:
        with contextlib.suppress(Exception):
            cleanup_task = await index.delete_document(document_id)
            await client.wait_for_task(
                cleanup_task.task_uid,
                timeout_in_ms=30_000,
                raise_for_status=True,
            )
        await client.aclose()
