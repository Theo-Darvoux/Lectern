"""Architectural test enforcing Option A: No DB-dependent route may return error responses directly.

If an endpoint depends on get_db(), all 4xx/5xx error paths MUST use `raise`
(e.g., `raise BadRequestError(...)` or `raise HTTPException(...)`) to ensure
that get_db()'s exception handler invokes session.rollback(). Returning a 4xx/5xx
Response object directly without raising is forbidden.
"""

import ast
from pathlib import Path

import pytest

ROUTERS_DIR = Path(__file__).parents[1] / "app" / "routers"


def _is_error_status_code(code_node: ast.AST) -> bool:
    """Check if an AST node represents an integer >= 400 or a 4xx/5xx HTTPStatus."""
    if isinstance(code_node, ast.Constant) and isinstance(code_node.value, int):
        return code_node.value >= 400
    if isinstance(code_node, ast.Attribute) and isinstance(code_node.value, ast.Name):
        if code_node.value.id == "HTTPStatus":
            # Matches HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND, etc.
            return True
    return False


def _check_return_for_error_response(node: ast.Return) -> str | None:
    """If the return statement returns a Response object with status_code >= 400, return details."""
    if node.value is None or not isinstance(node.value, ast.Call):
        return None

    call = node.value
    func_name = ""
    if isinstance(call.func, ast.Name):
        func_name = call.func.id
    elif isinstance(call.func, ast.Attribute):
        func_name = call.func.attr

    if "Response" not in func_name:
        return None

    for keyword in call.keywords:
        if keyword.arg == "status_code" and _is_error_status_code(keyword.value):
            return f"returns {func_name}(status_code={ast.unparse(keyword.value)})"

    return None


@pytest.mark.asyncio
async def test_no_db_routes_return_error_responses_directly() -> None:
    """Enforce that no route injecting get_db returns a 4xx/5xx Response directly without raising."""
    violations: list[str] = []

    for path in ROUTERS_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check if function accepts get_db dependency
                all_args = node.args.args + node.args.kwonlyargs
                has_get_db = any("get_db" in ast.dump(arg) for arg in all_args)

                if not has_get_db:
                    continue

                # TUS protocol specification handlers return raw TUS headers directly
                if path.name == "tus.py":
                    continue

                for child in ast.walk(node):
                    if isinstance(child, ast.Return):
                        reason = _check_return_for_error_response(child)
                        if reason:
                            rel_path = path.relative_to(ROUTERS_DIR.parent.parent)
                            violations.append(
                                f"{rel_path}:{child.lineno} in '{node.name}()': {reason}"
                            )

    assert not violations, (
        "Found DB-dependent endpoints returning error responses directly instead of raising:\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\nRoutes depending on get_db() MUST raise exceptions for 4xx/5xx errors to trigger session.rollback()."
    )
