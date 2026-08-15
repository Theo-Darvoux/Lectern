from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.routers.upload.direct import _claim_direct_upload_idempotency
from app.routers.upload.helpers import _create_upload_row

DATABASE_URL = os.environ.get("REVERT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="REVERT_TEST_DATABASE_URL is not configured"),
]


@pytest.mark.asyncio
async def test_concurrent_same_upload_id_loser_cannot_delete_winner_quarantine_object() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    upload_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    quarantine_key = f"quarantine/{user_id}/{upload_id}/same.txt"
    object_store: dict[str, bytes] = {}
    loser_touched_shared_resource = False

    async with sessions() as winner, sessions() as loser:
        assert await _claim_direct_upload_idempotency(winner, user_id, upload_id) is None

        object_store[quarantine_key] = b"winner"
        await _create_upload_row(
            upload_id=upload_id,
            user_id=user_id,
            quarantine_key=quarantine_key,
            filename="same.txt",
            mime_type="text/plain",
            size_bytes=len(object_store[quarantine_key]),
            db=winner,
        )

        async def competing_request():
            nonlocal loser_touched_shared_resource
            authoritative = await _claim_direct_upload_idempotency(loser, user_id, upload_id)
            if authoritative is None:
                loser_touched_shared_resource = True
                object_store.pop(quarantine_key, None)
            return authoritative

        competing = asyncio.create_task(competing_request())
        await asyncio.sleep(0.2)
        assert not competing.done(), "duplicate request crossed the idempotency boundary early"

        await winner.commit()
        authoritative = await asyncio.wait_for(competing, timeout=5)
        assert authoritative is not None
        assert authoritative.upload_id == upload_id
        assert authoritative.file_key == quarantine_key
        assert not loser_touched_shared_resource
        assert object_store[quarantine_key] == b"winner"
        await loser.rollback()

    await engine.dispose()


@pytest.mark.asyncio
async def test_rolled_back_upload_id_owner_allows_waiter_to_become_owner() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    upload_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    async with sessions() as first, sessions() as waiter:
        assert await _claim_direct_upload_idempotency(first, user_id, upload_id) is None
        competing = asyncio.create_task(
            _claim_direct_upload_idempotency(waiter, user_id, upload_id)
        )
        await asyncio.sleep(0.2)
        assert not competing.done()

        await first.rollback()
        assert await asyncio.wait_for(competing, timeout=5) is None
        await waiter.rollback()

    await engine.dispose()
