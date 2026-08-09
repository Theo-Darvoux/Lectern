"""ASGI request-body limits enforced before framework body parsing."""

import re
from collections.abc import Mapping, Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

BodyPatternLimit = tuple[str, re.Pattern[str], int]


class _BodyTooLargeError(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Bound selected request bodies, including chunked transfers.

    Route functions run only after FastAPI has parsed JSON or multipart input, so
    limits inside an endpoint are too late to protect process memory. This pure
    ASGI middleware counts bytes as they arrive and also rejects an oversized
    Content-Length without reading the body.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        path_limits: Mapping[str, int],
        pattern_limits: Sequence[BodyPatternLimit] = (),
    ) -> None:
        self.app = app
        self.path_limits = dict(path_limits)
        self.pattern_limits = tuple(pattern_limits)

    def _resolve_limit(self, scope: Scope) -> int | None:
        path = scope.get("path", "")
        method = scope.get("method", "").upper()

        exact = self.path_limits.get(path)
        if exact is not None:
            return exact

        for expected_method, pattern, limit in self.pattern_limits:
            if method == expected_method and pattern.fullmatch(path):
                return limit

        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._resolve_limit(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return


        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                content_length = int(value)
            except ValueError:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(
                    scope, receive, send
                )
                return
            if content_length < 0:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(
                    scope, receive, send
                )
                return
            if content_length > limit:
                await self._too_large(scope, receive, send, limit)
                return
            break

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLargeError:
            await self._too_large(scope, receive, send, limit)

    @staticmethod
    async def _too_large(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        await JSONResponse(
            {"detail": f"Request body too large (max {limit // (1024 * 1024)} MiB)"},
            status_code=413,
        )(scope, receive, send)
