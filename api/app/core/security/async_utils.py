"""Cancellation-safe helpers for work that cannot be interrupted mid-operation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


async def shielded_await[T](
    awaitable: Awaitable[T],
    *,
    description: str = "cleanup",
) -> T:
    """Await an operation to completion before re-delivering caller cancellation.

    This is intended for cleanup and ownership-transfer operations that must not be
    abandoned after they have started. Repeated cancellation is remembered while
    the child task continues to run. If both cancellation and a child failure occur,
    cancellation remains the externally visible result and the child failure is
    logged because the caller has already asked to stop.
    """

    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc

    if cancellation is not None:
        try:
            task.result()
        except BaseException:
            logger.exception("%s failed after caller cancellation", description)
        raise cancellation

    return task.result()


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
