"""Cancellation-safe helpers for work that cannot be interrupted mid-operation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

logger = logging.getLogger(__name__)


async def settle_awaitable[T](
    awaitable: Awaitable[T],
) -> tuple[T | None, BaseException | None, asyncio.CancelledError | None]:
    """Settle one awaitable without abandoning it on caller cancellation.

    Returns:
        (result, child_error, caller_cancellation)

    Child failure is returned, never raised directly.
    Caller cancellation is remembered while the child is allowed to settle.
    """
    task = asyncio.ensure_future(awaitable)
    caller_cancellation: asyncio.CancelledError | None = None

    while not task.done():
        try:
            await asyncio.shield(task)

        except asyncio.CancelledError as exc:
            current = asyncio.current_task()

            # If this task has an outstanding cancellation request, the
            # CancelledError came from our caller. Remember it and continue
            # waiting for the protected child.
            if current is not None and current.cancelling():
                caller_cancellation = caller_cancellation or exc
                continue

            # Otherwise the protected child cancelled itself.
            # task.result() below will classify it as the child error.
            break

        except BaseException:
            # asyncio.shield() propagates an ordinary child exception.
            # Do not let it escape here; inspect task.result() below.
            break

    try:
        return task.result(), None, caller_cancellation
    except BaseException as exc:
        return None, exc, caller_cancellation


async def shielded_await[T](
    awaitable: Awaitable[T],
    *,
    description: str = "cleanup",
) -> T:
    """Run an operation to settlement before re-delivering caller cancellation."""
    result, error, cancellation = await settle_awaitable(awaitable)

    if cancellation is not None:
        if error is not None:
            logger.error(
                "%s failed after caller cancellation",
                description,
                exc_info=(type(error), error, error.__traceback__),
            )
        raise cancellation

    if error is not None:
        raise error

    return cast(T, result)


async def shielded_to_thread[T](
    func: Callable[..., T],
    *args: Any,
    description: str | None = None,
    **kwargs: Any,
) -> T:
    """Run synchronous work without abandoning its worker thread on cancellation."""

    label = description or getattr(func, "__qualname__", repr(func))
    return await shielded_await(
        asyncio.to_thread(func, *args, **kwargs),
        description=f"thread worker {label}",
    )
