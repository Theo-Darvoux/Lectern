from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from starlette.types import Message, Scope

from app.core.http.body_limit import RequestBodyLimitMiddleware


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/limited",
        "raw_path": b"/limited",
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "server": ("test", 443),
        "client": ("127.0.0.1", 1234),
        "state": {},
    }


@pytest.mark.asyncio
async def test_body_limit_rejects_oversized_content_length_without_reading() -> None:
    app_called = False
    receive_called = False
    sent: list[Message] = []

    async def app(_scope: Scope, _receive: Callable[[], Awaitable[Message]], _send) -> None:
        nonlocal app_called
        app_called = True

    async def receive() -> Message:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(app, path_limits={"/limited": 4})
    await middleware(_scope([(b"content-length", b"5")]), receive, send)

    assert not app_called
    assert not receive_called
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_limit_counts_chunked_request_bytes() -> None:
    messages = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"45", "more_body": False},
        ]
    )
    sent: list[Message] = []

    async def receive() -> Message:
        return next(messages)  # type: ignore[return-value]

    async def app(_scope: Scope, limited_receive, _send) -> None:
        await limited_receive()
        await limited_receive()

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(app, path_limits={"/limited": 4})
    await middleware(_scope(), receive, send)

    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_limit_matches_regex_pattern_with_method() -> None:
    import re

    app_called = False
    sent: list[Message] = []

    async def app(_scope: Scope, _receive: Callable[[], Awaitable[Message]], _send) -> None:
        nonlocal app_called
        app_called = True

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    pattern = re.compile(r"/api/materials/[^/]+/text-content")
    middleware = RequestBodyLimitMiddleware(
        app,
        path_limits={},
        pattern_limits=[("POST", pattern, 10)],
    )
    scope = _scope([(b"content-length", b"15")])
    scope["path"] = "/api/materials/abc-123/text-content"
    scope["method"] = "POST"

    await middleware(scope, receive, send)

    assert not app_called
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_limit_pattern_ignores_wrong_method() -> None:
    import re

    app_called = False

    async def app(_scope: Scope, _receive: Callable[[], Awaitable[Message]], _send) -> None:
        nonlocal app_called
        app_called = True

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        pass

    pattern = re.compile(r"/api/materials/[^/]+/text-content")
    middleware = RequestBodyLimitMiddleware(
        app,
        path_limits={},
        pattern_limits=[("POST", pattern, 10)],
    )
    scope = _scope([(b"content-length", b"15")])
    scope["path"] = "/api/materials/abc-123/text-content"
    scope["method"] = "GET"

    await middleware(scope, receive, send)

    assert app_called

