import asyncio
import logging
import sys
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("wait_for_db")


async def wait_for_db() -> None:
    """Wait for the database to be ready to accept queries."""
    engine = create_async_engine(settings.database_url)
    start_time = time.time()
    timeout = 60

    logger.info("Waiting for database to be ready (timeout=%ds)...", timeout)

    while True:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database is ready!")
            break
        except Exception as e:
            if time.time() - start_time > timeout:
                logger.error("Timed out waiting for database after %ds: %s", timeout, e)
                sys.exit(1)

            # Only log the error type and a short message to avoid log spam
            logger.info("Database not ready yet, retrying in 1s... (%s)", type(e).__name__)
            await asyncio.sleep(1)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(wait_for_db())
