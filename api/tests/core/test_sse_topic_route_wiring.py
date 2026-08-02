import ast
import uuid
from pathlib import Path

import pytest

from app.core.common.exceptions import NotFoundError

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
        any(keyword.arg == "owner_keys" for keyword in call.keywords) for call in registrations
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


def test_directory_stream_keeps_root_string_contract() -> None:
    source = (_API_ROOT / "app/routers/directories.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    endpoint = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "directory_event_stream"
    )
    id_arg = next(arg for arg in endpoint.args.args if arg.arg == "id")

    assert isinstance(id_arg.annotation, ast.Name)
    assert id_arg.annotation.id == "str"


def _load_directory_topic_normalizer():
    source = (_API_ROOT / "app/routers/directories.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_normalize_directory_topic"
    )
    namespace = {"uuid": uuid, "NotFoundError": NotFoundError}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<directory-topic>", "exec"),
        namespace,
    )
    return namespace["_normalize_directory_topic"]


def test_directory_topic_accepts_root_and_normalizes_uuid() -> None:
    normalize_directory_topic = _load_directory_topic_normalizer()
    value = "550E8400-E29B-41D4-A716-446655440000"

    assert normalize_directory_topic("root") == "root"
    assert normalize_directory_topic(value) == "550e8400-e29b-41d4-a716-446655440000"


def test_directory_topic_rejects_other_non_uuid_values() -> None:
    normalize_directory_topic = _load_directory_topic_normalizer()

    with pytest.raises(NotFoundError, match="Directory not found"):
        normalize_directory_topic("not-a-directory")
