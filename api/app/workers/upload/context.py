from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class RedisClient(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, _value: Any, **_kwargs: Any) -> Any: ...
    async def delete(self, *_keys: str) -> Any: ...
    async def publish(self, _channel: str, _message: str) -> Any: ...
    async def lpush(self, key: str, _value: str) -> Any: ...
    async def ltrim(self, key: str, _start: int, _stop: int) -> Any: ...
    async def exists(self, key: str) -> bool: ...
    async def zadd(self, key: str, _mapping: dict[str, float]) -> Any: ...
    async def zrem(self, key: str, *_members: str) -> Any: ...
    async def rpush(self, key: str, *_values: str) -> Any: ...
    async def expire(self, key: str, _seconds: int) -> Any: ...
    async def eval(self, _script: Any, _numkeys: int, *_keys_and_args: Any) -> Any: ...


@dataclass(frozen=True)
class WorkerContext:
    """Structured context for upload worker tasks, replacing raw dict."""

    redis: RedisClient
    db_sessionmaker: async_sessionmaker[AsyncSession] | None
    job_try: int = 1
    scanner: Any | None = None
    yara_compiled_at: float = 0.0

    @classmethod
    def from_arq_ctx(cls, ctx: dict[str, Any]) -> "WorkerContext":
        return cls(
            redis=ctx["redis"],
            db_sessionmaker=ctx.get("db_sessionmaker"),
            job_try=ctx.get("job_try", 1),
            scanner=ctx.get("scanner"),
            yara_compiled_at=ctx.get("yara_compiled_at", 0.0),
        )

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)
