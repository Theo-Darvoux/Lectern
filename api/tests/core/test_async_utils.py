import asyncio

import pytest

from app.core.security.async_utils import settle_awaitable, shielded_await


@pytest.mark.asyncio
async def test_settle_awaitable_returns_child_result() -> None:
    async def succeed() -> str:
        return "ok"

    result, error, cancellation = await settle_awaitable(succeed())

    assert result == "ok"
    assert error is None
    assert cancellation is None


@pytest.mark.asyncio
async def test_settle_awaitable_returns_child_exception() -> None:
    async def fail() -> None:
        raise ValueError("boom")

    result, error, cancellation = await settle_awaitable(fail())

    assert result is None
    assert isinstance(error, ValueError)
    assert str(error) == "boom"
    assert cancellation is None


@pytest.mark.asyncio
async def test_settle_awaitable_reports_child_self_cancellation_as_child_error() -> None:
    async def self_cancel() -> None:
        raise asyncio.CancelledError()

    result, error, cancellation = await settle_awaitable(self_cancel())

    assert result is None
    assert isinstance(error, asyncio.CancelledError)
    assert cancellation is None


@pytest.mark.asyncio
async def test_settle_awaitable_waits_after_caller_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def child() -> str:
        started.set()
        await release.wait()
        return "done"

    async def runner():
        return await settle_awaitable(child())

    task = asyncio.create_task(runner())

    await started.wait()
    task.cancel()
    # Yield to let the cancellation propagate into the shield loop.
    await asyncio.sleep(0)
    release.set()

    result, error, cancellation = await task

    assert result == "done"
    assert error is None
    assert isinstance(cancellation, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_settle_awaitable_survives_repeated_real_task_cancellation() -> None:
    """Every caller Task.cancel() must be deferred until the protected child settles."""
    started = asyncio.Event()
    release = asyncio.Event()
    settled = asyncio.Event()

    async def child() -> str:
        started.set()
        await release.wait()
        settled.set()
        return "done"

    async def runner():
        return await settle_awaitable(child())

    task = asyncio.create_task(runner())
    await started.wait()

    task.cancel("first cancellation")
    await asyncio.sleep(0)
    assert not task.done()
    assert not settled.is_set()

    task.cancel("second cancellation")
    await asyncio.sleep(0)
    assert not task.done()
    assert not settled.is_set()

    task.cancel("third cancellation")
    await asyncio.sleep(0)
    assert not task.done()
    assert not settled.is_set()

    release.set()
    result, error, cancellation = await task

    assert settled.is_set()
    assert result == "done"
    assert error is None
    assert isinstance(cancellation, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_settle_awaitable_caller_cancel_then_child_self_cancels() -> None:
    """Caller cancellation + child self-cancel: both are correctly classified.

    The caller_cancellation slot should hold the caller's CancelledError,
    while the child's self-cancellation should appear in the child error slot.
    This tests the current_task().cancelling() distinction.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def child() -> None:
        started.set()
        await release.wait()
        # After being released, the child deliberately cancels itself.
        raise asyncio.CancelledError("child cancelled itself")

    async def runner():
        return await settle_awaitable(child())

    task = asyncio.create_task(runner())

    await started.wait()
    task.cancel()
    # Yield to let the caller cancellation propagate into the shield loop.
    await asyncio.sleep(0)
    release.set()

    result, error, cancellation = await task

    assert result is None
    assert isinstance(error, asyncio.CancelledError)
    assert isinstance(cancellation, asyncio.CancelledError)
    # The child error and caller cancellation should be distinct objects.
    assert error is not cancellation


@pytest.mark.asyncio
async def test_shielded_await_redelivers_cancellation_even_if_child_later_fails(
    caplog,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def child() -> None:
        started.set()
        await release.wait()
        raise OSError("cleanup failed")

    task = asyncio.create_task(shielded_await(child(), description="test cleanup"))

    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert "test cleanup failed after caller cancellation" in caplog.text
