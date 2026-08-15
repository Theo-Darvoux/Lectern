import ast
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.common.exceptions import NotFoundError
from app.core.database.redis import RedisLockTimeoutError
from app.core.events.sse import SSECapacityError
from app.routers import events
from app.routers.events import parse_master_topics

_API_ROOT = Path(__file__).resolve().parents[2]


def test_persistent_live_updates_expose_only_the_master_sse_route() -> None:
    legacy_route_files = (
        "app/routers/annotations.py",
        "app/routers/directories.py",
        "app/routers/notifications.py",
        "app/routers/pull_requests.py",
    )

    for relative_path in legacy_route_files:
        source = (_API_ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        sse_routes = [
            decorator
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "get"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
            and str(decorator.args[0].value).endswith("/sse")
        ]
        assert not sse_routes, f"{relative_path} still exposes a legacy SSE route"


def test_master_topics_are_normalized_deduplicated_and_include_user_prs() -> None:
    user_id = uuid.uuid4()
    material_id = uuid.uuid4()

    assert parse_master_topics(
        user_id,
        ["directory:root", f"material:{material_id}", f"material:{material_id}"],
    ) == {
        "pull_requests": f"pr_updates:{user_id}",
        "directory:root": "root",
        f"material:{material_id}": str(material_id),
    }


@pytest.mark.parametrize("topic", ["unknown:value", "material:not-a-uuid", "directory:nope"])
def test_master_topics_reject_unknown_or_malformed_values(topic: str) -> None:
    with pytest.raises(NotFoundError, match="Live-update topic not found"):
        parse_master_topics(uuid.uuid4(), [topic])


@pytest.mark.asyncio
async def test_master_lease_is_held_for_dependency_lifetime(monkeypatch) -> None:
    lifecycle: list[str] = []

    @asynccontextmanager
    async def fake_lock(*args, **kwargs):
        lifecycle.append("acquired")
        try:
            yield
        finally:
            lifecycle.append("released")

    monkeypatch.setattr(events, "redis_lock", fake_lock)
    lease = events._hold_master_lease(SimpleNamespace(id=uuid.uuid4()), object())

    assert await anext(lease) is None
    assert lifecycle == ["acquired"]

    await lease.aclose()
    assert lifecycle == ["acquired", "released"]


@pytest.mark.asyncio
async def test_duplicate_master_lease_is_rejected_before_streaming(monkeypatch) -> None:
    @asynccontextmanager
    async def unavailable_lock(*args, **kwargs):
        raise RedisLockTimeoutError("already held")
        yield

    monkeypatch.setattr(events, "redis_lock", unavailable_lock)
    lease = events._hold_master_lease(SimpleNamespace(id=uuid.uuid4()), object())

    with pytest.raises(SSECapacityError, match="already active"):
        await anext(lease)
