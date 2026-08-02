from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.database.post_commit import (
    PostCommitKey,
    add_post_commit_job,
    add_post_commit_sse,
    dispatch_pending_outbox,
    dispatch_post_commit_actions,
    outbox_kwargs,
    persist_post_commit_jobs,
)
from app.core.events.coalesce import coalesce_index_jobs


def test_coalesce_index_jobs_material():
    jobs = [
        ("index_material", "m1"),
        ("index_material", "m2"),
        ("delete_storage_objects", ["file1"]),
        ("index_material", "m3"),
    ]
    result = coalesce_index_jobs(jobs)
    assert result == [
        ("index_materials_batch", ["m1", "m2"]),
        ("delete_storage_objects", ["file1"]),
        ("index_material", "m3"),
    ]


def test_coalesce_index_jobs_directory():
    jobs = [
        ("index_directory", "d1"),
        ("index_directory", "d2"),
        ("index_directory", "d3"),
    ]
    result = coalesce_index_jobs(jobs)
    assert result == [
        ("index_directories_batch", ["d1", "d2", "d3"]),
    ]


def test_add_post_commit_job_and_sse_helpers():
    mock_session = MagicMock()
    mock_session.info = {}

    add_post_commit_job(mock_session, ("index_material", "m1"))
    add_post_commit_sse(mock_session, "topic_test", {"action": "update"})

    assert mock_session.info[PostCommitKey.JOBS] == [("index_material", "m1")]
    assert mock_session.info[PostCommitKey.SSE] == [("topic_test", {"action": "update"})]


@pytest.mark.asyncio
async def test_dispatch_post_commit_actions():
    mock_session = MagicMock()
    mock_session.info = {
        PostCommitKey.SSE: [("topic1", {"event": 1})],
    }

    with (
        patch("app.core.database.post_commit.broadcast_to_topic") as mock_broadcast,
        patch(
            "app.core.database.post_commit.dispatch_pending_outbox", new_callable=AsyncMock
        ) as mock_dispatch,
    ):
        await dispatch_post_commit_actions(mock_session)

        mock_broadcast.assert_called_once_with("topic1", {"event": 1})
        mock_dispatch.assert_awaited_once_with(mock_session)


@pytest.mark.asyncio
async def test_persist_post_commit_jobs_writes_coalesced_outbox_rows():
    session = MagicMock()
    session.info = {
        PostCommitKey.JOBS: [("index_material", "m1"), ("index_material", "m2")],
        PostCommitKey.JOB_KEYS: set(),
    }

    count = await persist_post_commit_jobs(session)

    assert count == 1
    row = session.add.call_args.args[0]
    assert row.job_name == "index_materials_batch"
    assert row.args == [["m1", "m2"]]
    assert PostCommitKey.JOBS not in session.info


@pytest.mark.asyncio
async def test_persist_post_commit_jobs_normalizes_explicit_json_scalars():
    session = MagicMock()
    identifier = uuid4()
    session.info = {
        PostCommitKey.JOBS: [
            (
                "process_upload",
                outbox_kwargs(upload_id=identifier, trace_context={"sampled": True}),
            )
        ]
    }

    await persist_post_commit_jobs(session)

    row = session.add.call_args.args[0]
    assert row.args == [
        {"__outbox_kwargs__": {"upload_id": str(identifier), "trace_context": {"sampled": True}}}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job, error",
    [
        ((), "non-empty tuples"),
        (("not_registered",), "unknown outbox job"),
        (("index_material", object()), "unsupported outbox argument type"),
        (("index_material", {1: "value"}), "string keys"),
        (("index_material", float("nan")), "non-finite"),
    ],
)
async def test_persist_post_commit_jobs_rejects_malformed_jobs(job, error):
    session = MagicMock()
    session.info = {PostCommitKey.JOBS: [job]}

    with pytest.raises((TypeError, ValueError), match=error):
        await persist_post_commit_jobs(session)

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_failed_outbox_delivery_remains_pending():
    from app.models.outbox import OutboxJob

    row = OutboxJob(job_name="index_material", args=["m1"])
    rows = MagicMock()
    rows.all.return_value = [row]
    session = MagicMock()
    session.scalars = AsyncMock(return_value=rows)
    session.commit = AsyncMock()
    pool = AsyncMock()
    pool.enqueue_job.side_effect = OSError("redis down")

    with patch("app.core.database.redis.arq_pool", pool):
        delivered = await dispatch_pending_outbox(session)

    assert delivered == 0
    assert row.delivered_at is None
    assert row.attempts == 1
    assert row.last_error == "redis down"
    session.commit.assert_awaited_once()


def test_post_commit_key_enum_members():
    assert PostCommitKey.JOBS == "post_commit_jobs"
    assert PostCommitKey.SSE == "post_commit_sse_broadcasts"
    assert PostCommitKey.JOB_KEYS == "post_commit_job_keys"
    assert PostCommitKey.DEINDEX_KEYS == "post_commit_deindex_keys"
