from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockError

from app.core.database.redis import (
    RedisConcurrencyError,
    RedisLockTimeoutError,
    RedisSemaphoreTimeoutError,
    RedisSemaphoreUnavailableError,
    get_redis,
    redis_lock,
    redis_semaphore,
)


@pytest.mark.asyncio
async def test_get_redis():
    async for client in get_redis():
        assert client is not None


@pytest.mark.asyncio
async def test_redis_lock_successful_acquisition_and_release():
    mock_redis = AsyncMock()
    mock_lock = AsyncMock()
    mock_lock.acquire.return_value = True
    mock_redis.lock = MagicMock(return_value=mock_lock)

    async with redis_lock(mock_redis, "test_lock", timeout=1.0):
        pass

    mock_redis.lock.assert_called_once_with("lock:test_lock", timeout=30, sleep=0.1)
    mock_lock.acquire.assert_called_once_with(blocking_timeout=1.0)
    mock_lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_redis_lock_renews_lease_while_body_is_running():
    mock_redis = AsyncMock()
    mock_lock = AsyncMock()
    mock_lock.acquire.return_value = True
    mock_redis.lock = MagicMock(return_value=mock_lock)

    async with redis_lock(mock_redis, "renewed", expire=0.03):
        import asyncio

        await asyncio.sleep(0.025)

    mock_lock.extend.assert_awaited()


@pytest.mark.asyncio
async def test_redis_lock_reports_lost_ownership_on_release():
    mock_redis = AsyncMock()
    mock_lock = AsyncMock()
    mock_lock.acquire.return_value = True
    mock_lock.release.side_effect = LockError("Cannot release an unlocked lock")
    mock_redis.lock = MagicMock(return_value=mock_lock)

    with pytest.raises(RedisConcurrencyError, match="Lost lock ownership"):
        async with redis_lock(mock_redis, "test_lock", timeout=1.0):
            pass

    mock_lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_redis_lock_timeout_raises_error():
    mock_redis = AsyncMock()
    mock_lock = AsyncMock()
    mock_lock.acquire.return_value = False
    mock_redis.lock = MagicMock(return_value=mock_lock)

    with pytest.raises(RedisLockTimeoutError, match="Could not acquire lock test_lock") as exc_info:
        async with redis_lock(mock_redis, "test_lock", timeout=0.1, retry_interval=0.02):
            pass

    assert isinstance(exc_info.value, TimeoutError)
    assert isinstance(exc_info.value, RedisConcurrencyError)


@pytest.mark.asyncio
async def test_redis_semaphore_successful_acquisition():
    mock_redis = AsyncMock()
    mock_redis.zrem.return_value = 1

    with patch("app.core.database.redis._semaphore_script", new_callable=AsyncMock) as mock_sem:
        mock_sem.return_value = 1
        async with redis_semaphore(mock_redis, "test_sem", limit=2, timeout=1.0):
            pass

        mock_sem.assert_called_once()
        mock_redis.zrem.assert_called_once()


@pytest.mark.asyncio
async def test_redis_semaphore_renews_lease_while_body_is_running():
    mock_redis = AsyncMock()
    mock_redis.zrem.return_value = 1

    with patch("app.core.database.redis._semaphore_script", new_callable=AsyncMock) as mock_sem:
        mock_sem.return_value = 1
        async with redis_semaphore(mock_redis, "renewed", limit=1, expire=0.03):
            import asyncio

            await asyncio.sleep(0.025)

        assert mock_sem.await_count >= 2


@pytest.mark.asyncio
async def test_redis_semaphore_preserves_body_timeout_error():
    mock_redis = AsyncMock()
    mock_redis.zrem.return_value = 1

    with patch("app.core.database.redis._semaphore_script", new_callable=AsyncMock) as mock_sem:
        mock_sem.return_value = 1
        with pytest.raises(TimeoutError, match="body deadline"):
            async with redis_semaphore(mock_redis, "body_timeout", limit=1):
                raise TimeoutError("body deadline")


@pytest.mark.asyncio
async def test_redis_semaphore_preserves_redis_unavailable_error():
    mock_redis = AsyncMock()

    with patch("app.core.database.redis._semaphore_script", new_callable=AsyncMock) as mock_sem:
        mock_sem.side_effect = RedisConnectionError("down")
        with pytest.raises(RedisSemaphoreUnavailableError):
            async with redis_semaphore(mock_redis, "unavailable", limit=1):
                pass


@pytest.mark.asyncio
async def test_redis_semaphore_reports_expired_holder_on_release():
    mock_redis = AsyncMock()
    mock_redis.zrem.return_value = 0

    with patch("app.core.database.redis._semaphore_script", new_callable=AsyncMock) as mock_sem:
        mock_sem.return_value = 1
        with pytest.raises(RedisConcurrencyError, match="before release"):
            async with redis_semaphore(mock_redis, "expired", limit=1):
                pass


@pytest.mark.asyncio
async def test_redis_semaphore_timeout_raises_error():
    mock_redis = AsyncMock()

    with patch("app.core.database.redis._semaphore_script", new_callable=AsyncMock) as mock_sem:
        mock_sem.return_value = 0
        with pytest.raises(
            RedisSemaphoreTimeoutError, match="Could not acquire semaphore test_sem"
        ) as exc_info:
            async with redis_semaphore(
                mock_redis, "test_sem", limit=2, timeout=0.1, retry_interval=0.02
            ):
                pass

        assert isinstance(exc_info.value, TimeoutError)
        assert isinstance(exc_info.value, RedisConcurrencyError)


def test_build_redis_settings():
    from app.core.database.redis import build_redis_settings

    settings_obj = build_redis_settings()
    assert settings_obj.conn_timeout == 10
    assert settings_obj.conn_retries == 10


@pytest.mark.asyncio
async def test_init_and_close_arq_pool():
    import app.core.database.redis as redis_mod

    mock_pool = AsyncMock()
    with patch("app.core.database.redis.create_pool", return_value=mock_pool):
        await redis_mod.init_arq_pool()
        assert redis_mod.arq_pool == mock_pool

        await redis_mod.close_arq_pool()
        mock_pool.close.assert_called_once()
        assert redis_mod.arq_pool is None


@pytest.mark.asyncio
async def test_close_redis_client():
    from app.core.database.redis import close_redis_client

    with patch("app.core.database.redis.redis_client.aclose", new_callable=AsyncMock) as mock_close:
        await close_redis_client()
        mock_close.assert_called_once()


@pytest.mark.asyncio
async def test_redis_semaphore_repeated_cancellation_waits_for_release():
    """Repeated caller cancellation cannot abandon holder removal."""
    import asyncio

    mock_redis = AsyncMock()
    release_started = asyncio.Event()
    allow_release = asyncio.Event()

    async def delayed_zrem(*_args):
        release_started.set()
        await allow_release.wait()
        return 1

    mock_redis.zrem.side_effect = delayed_zrem

    async def mock_script(*_args, **_kwargs):
        return 1

    entered = asyncio.Event()

    async def guarded_body() -> None:
        with patch("app.core.database.redis._semaphore_script", side_effect=mock_script):
            async with redis_semaphore(mock_redis, "cancel_cleanup", limit=1, expire=10):
                entered.set()
                await asyncio.Event().wait()

    task = asyncio.create_task(guarded_body())
    await entered.wait()
    task.cancel()
    await release_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    allow_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    mock_redis.zrem.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_cancellation_wins_lease_loss_race():
    """A caller cancellation racing renewal failure must not become a Redis error."""
    import asyncio

    mock_redis = AsyncMock()
    mock_redis.zrem.return_value = 1
    renewal_started = asyncio.Event()

    async def mock_script(*_args, **kwargs):
        operation = kwargs["args"][2]
        if operation == "renew":
            renewal_started.set()
            await asyncio.sleep(0)
            return 0
        return 1

    entered = asyncio.Event()

    async def guarded_body() -> None:
        with patch("app.core.database.redis._semaphore_script", side_effect=mock_script):
            async with redis_semaphore(mock_redis, "cancel_race", limit=1, expire=0.03):
                entered.set()
                await asyncio.Event().wait()

    task = asyncio.create_task(guarded_body())
    await entered.wait()
    await renewal_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    mock_redis.zrem.assert_awaited_once()
