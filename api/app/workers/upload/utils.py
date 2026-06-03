import asyncio
import logging
from collections.abc import Awaitable

logger = logging.getLogger(__name__)


async def parallel_tasks[T](
    *tasks: Awaitable[T],
    return_exceptions: bool = True,
) -> list[T | BaseException]:
    """Run multiple tasks in parallel and return results/exceptions."""
    return await asyncio.gather(*tasks, return_exceptions=return_exceptions)
