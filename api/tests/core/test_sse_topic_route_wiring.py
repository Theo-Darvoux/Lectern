import ast
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[2]
_TOPIC_ROUTE_FILES = (
    "app/routers/annotations.py",
    "app/routers/directories.py",
    "app/routers/pull_requests.py",
)


@pytest.mark.parametrize("relative_path", _TOPIC_ROUTE_FILES)
def test_topic_stream_routes_supply_explicit_owner_key(relative_path: str) -> None:
    source = (_API_ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    registrations = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register_topic_queue"
    ]

    assert registrations, f"{relative_path} must register at least one topic stream"
    assert all(
        any(keyword.arg == "owner_key" for keyword in call.keywords)
        for call in registrations
    )


def test_directory_stream_has_handshake_rate_limit() -> None:
    source = (_API_ROOT / "app/routers/directories.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    endpoint = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "directory_event_stream"
    )

    assert any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "limiter"
        and decorator.func.attr == "limit"
        for decorator in endpoint.decorator_list
    )
