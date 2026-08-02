"""Database engines, sessions, and Redis integrations with lazy exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["async_session_factory", "engine"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("app.core.database.database"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
