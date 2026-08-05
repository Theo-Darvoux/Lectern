from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import ClientSession, web
from botocore.exceptions import ClientError

from app.core.storage.s3 import S3Backend


class _AiohttpBody:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def read(self, size: int = -1) -> bytes:
        return await self._response.content.read(size)

    def close(self) -> None:
        self._response.close()


class _ChunkedBody:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    async def read(self, _size: int = -1) -> bytes:
        return next(self._chunks, b"")

    def close(self) -> None:
        self.closed = True


@asynccontextmanager
async def _client_context(client: Any) -> AsyncIterator[Any]:
    yield client


@pytest.mark.asyncio
async def test_read_object_bytes_collects_transport_short_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _ChunkedBody([b"<", b"sv", b"g data", b""])

    class Client:
        exceptions = SimpleNamespace(ClientError=ClientError)

        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            return {"Body": body, "ContentLength": 9}

    backend = S3Backend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(Client()))

    assert await backend.read_object_bytes("key", 9) == b"<svg data"
    assert body.closed


@pytest.mark.asyncio
async def test_read_object_bytes_collects_real_chunked_http_response(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port: int,
) -> None:
    payload = b"<svg xmlns='http://www.w3.org/2000/svg'>"

    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=206,
            headers={"Content-Length": str(len(payload))},
        )
        await response.prepare(request)
        for chunk in (payload[:1], payload[1:4], payload[4:11], payload[11:]):
            await response.write(chunk)
            await asyncio.sleep(0.01)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/object", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()

    session = ClientSession()

    class Client:
        exceptions = SimpleNamespace(ClientError=ClientError)

        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            response = await session.get(f"http://127.0.0.1:{unused_tcp_port}/object")
            return {"Body": _AiohttpBody(response), "ContentLength": len(payload)}

    backend = S3Backend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(Client()))
    try:
        assert await backend.read_object_bytes("key", len(payload)) == payload
    finally:
        await session.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_read_object_bytes_stops_at_short_object_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _ChunkedBody([b"ab", b"c", b""])

    class Client:
        exceptions = SimpleNamespace(ClientError=ClientError)

        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            return {"Body": body, "ContentLength": 3}

    backend = S3Backend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(Client()))

    assert await backend.read_object_bytes("key", 2048) == b"abc"
    assert body.closed
