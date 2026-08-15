import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.database.post_commit import (
    PostCommitKey,
    add_post_commit_job,
    add_post_commit_sse,
    add_post_commit_user_sse,
    dispatch_pending_outbox,
    dispatch_post_commit_actions,
    finalize_transaction_callbacks,
    outbox_kwargs,
    persist_post_commit_jobs,
    rollback_transaction_callbacks,
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
    add_post_commit_user_sse(mock_session, uuid4(), {"type": "notification"})

    assert mock_session.info[PostCommitKey.JOBS] == [("index_material", "m1")]
    assert mock_session.info[PostCommitKey.SSE] == [("topic_test", {"action": "update"})]
    assert len(mock_session.info[PostCommitKey.USER_SSE]) == 1


@pytest.mark.asyncio
async def test_rollback_callbacks_all_settle_before_cancellation_is_redelivered() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    later = AsyncMock()

    async def slow_cleanup() -> None:
        started.set()
        await release.wait()

    session = MagicMock()
    session.info = {
        PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS: [later, slow_cleanup],
        PostCommitKey.TRANSACTION_COMMIT_CALLBACKS: [],
    }
    rollback = asyncio.create_task(rollback_transaction_callbacks(session))
    await started.wait()
    rollback.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await rollback
    later.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_callbacks_all_settle_before_cancellation_is_redelivered() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    later = AsyncMock()

    async def slow_cleanup() -> None:
        started.set()
        await release.wait()

    session = MagicMock()
    session.info = {
        PostCommitKey.TRANSACTION_COMMIT_CALLBACKS: [slow_cleanup, later],
        PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS: [],
    }
    finalize = asyncio.create_task(finalize_transaction_callbacks(session))
    await started.wait()
    finalize.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await finalize
    later.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_post_commit_actions():
    user_id = uuid4()
    mock_session = MagicMock()
    mock_session.info = {
        PostCommitKey.SSE: [("topic1", {"event": 1})],
        PostCommitKey.USER_SSE: [(user_id, {"type": "notification"})],
    }

    with (
        patch("app.core.database.post_commit.broadcast_to_topic") as mock_broadcast,
        patch("app.core.database.post_commit.broadcast_to_user") as mock_user_broadcast,
        patch(
            "app.core.database.post_commit.dispatch_pending_outbox", new_callable=AsyncMock
        ) as mock_dispatch,
    ):
        await dispatch_post_commit_actions(mock_session)

        mock_broadcast.assert_called_once_with("topic1", {"event": 1})
        mock_user_broadcast.assert_called_once_with(user_id, {"type": "notification"})
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
    assert PostCommitKey.USER_SSE == "post_commit_user_sse_broadcasts"


@pytest.mark.asyncio
async def test_rollback_callback_failure_does_not_skip_remaining_callbacks() -> None:
    later = AsyncMock()

    async def failing_cleanup() -> None:
        raise OSError("s3 delete failed")

    session = MagicMock()
    session.info = {
        # reversed() means failing_cleanup runs first.
        PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS: [
            later,
            failing_cleanup,
        ],
        PostCommitKey.TRANSACTION_COMMIT_CALLBACKS: [],
    }

    with pytest.raises(
        RuntimeError,
        match="1 external transaction compensation callback",
    ):
        await rollback_transaction_callbacks(session)

    later.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_callback_failure_does_not_skip_remaining_callbacks(
    caplog,
) -> None:
    later = AsyncMock()

    async def failing_finalizer() -> None:
        raise OSError("cleanup failed")

    session = MagicMock()
    session.info = {
        PostCommitKey.TRANSACTION_COMMIT_CALLBACKS: [
            failing_finalizer,
            later,
        ],
        PostCommitKey.TRANSACTION_ROLLBACK_CALLBACKS: [],
    }

    await finalize_transaction_callbacks(session)

    later.assert_awaited_once()
    assert "External transaction finalization failed" in caplog.text
