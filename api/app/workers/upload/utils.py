import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

logger = logging.getLogger("wikint")


async def parallel_tasks[T](
    *tasks: Awaitable[T],
    return_exceptions: bool = True,
) -> list[T | BaseException]:
    """Run multiple tasks in parallel and return results/exceptions."""
    return await asyncio.gather(*tasks, return_exceptions=return_exceptions)


def check_task_exceptions(
    results: list[Any | BaseException],
    task_names: list[str],
    upload_id: str,
) -> None:
    """Helper to log multiple failures and raise the first critical one if needed."""
    exceptions = [res for res in results if isinstance(res, BaseException)]
    if not exceptions:
        return

    for res, name in zip(results, task_names, strict=False):
        if isinstance(res, BaseException):
            logger.error("Task '%s' failed for upload %s: %r", name, upload_id, res, exc_info=res)
