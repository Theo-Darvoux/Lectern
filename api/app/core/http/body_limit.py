"""ASGI request-body limits enforced before framework body parsing."""

from collections.abc import Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLargeError(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Bound selected request bodies, including chunked transfers.

    Route functions run only after FastAPI has parsed JSON or multipart input, so
    limits inside an endpoint are too late to protect process memory. This pure
    ASGI middleware counts bytes as they arrive and also rejects an oversized
    Content-Length without reading the body.
    """

    def __init__(self, app: ASGIApp, *, path_limits: Mapping[str, int]) -> None:
        self.app = app
        self.path_limits = dict(path_limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.path_limits.get(scope.get("path", ""))
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
