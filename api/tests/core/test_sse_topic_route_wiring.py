import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.core.events.sse as sse
from app.routers import events
from app.routers.events import parse_topics

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


def test_upload_progress_has_no_dedicated_stream_route() -> None:
    source = (_API_ROOT / "app/routers/upload/file_status.py").read_text(encoding="utf-8")
    assert "EventSourceResponse" not in source
    assert '"/events/' not in source


def test_topics_are_normalized_deduplicated_and_include_user_channels() -> None:
    user_id = uuid.uuid4()
    material_id = uuid.uuid4()

    assert parse_topics(
        user_id,
        ["directory:root", f"material:{material_id}", f"material:{material_id}"],
    ) == {
        "notifications": f"notifications:{user_id}",
        "pull_requests": f"pr_updates:{user_id}",
        "uploads": f"uploads:{user_id}",
        "directory:root": "root",
        f"material:{material_id}": str(material_id),
    }


@pytest.mark.parametrize("topic", ["unknown:value", "material:not-a-uuid", "directory:nope", ""])
def test_invalid_topics_are_ignored_without_losing_user_channels(topic: str) -> None:
    user_id = uuid.uuid4()

    assert parse_topics(user_id, [topic]) == {
        "notifications": f"notifications:{user_id}",
        "pull_requests": f"pr_updates:{user_id}",
        "uploads": f"uploads:{user_id}",
    }


def test_entity_topics_are_capped() -> None:
    topics = [f"material:{uuid.uuid4()}" for _ in range(events._MAX_ENTITY_TOPICS + 5)]

    parsed = parse_topics(uuid.uuid4(), topics)

    assert sum(channel.startswith("material:") for channel in parsed) == events._MAX_ENTITY_TOPICS


@pytest.mark.asyncio
async def test_deleted_entities_are_dropped_without_failing_the_stream(monkeypatch) -> None:
    live_id, deleted_id = uuid.uuid4(), uuid.uuid4()

    async def existing_ids(_db: object, _model: object, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        return ids & {live_id}

    monkeypatch.setattr(events, "_existing_ids", existing_ids)
    user_id = uuid.uuid4()

    filtered = await events._drop_deleted_entities(
        SimpleNamespace(),  # type: ignore[arg-type]
        parse_topics(user_id, [f"directory:{live_id}", f"material:{deleted_id}", "directory:root"]),
    )

    assert filtered == {
        "notifications": f"notifications:{user_id}",
        "pull_requests": f"pr_updates:{user_id}",
        "uploads": f"uploads:{user_id}",
        "directory:root": "root",
        f"directory:{live_id}": str(live_id),
    }


@pytest.mark.asyncio
async def test_stream_slot_releases_stream_even_if_body_never_started() -> None:
    user_id = uuid.uuid4()
    slot_dependency = events._stream_slot()
    slot = await anext(slot_dependency)
    slot.stream = sse.register_stream(user_id, "browser-a", {})

    await slot_dependency.aclose()

    assert user_id not in sse._user_streams
