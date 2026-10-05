"""Gunicorn worker that drains SSE streams when it recycles on ``--max-requests``.

uvicorn's max-requests path ends ``main_loop`` without setting
``Server.should_exit``. sse-starlette only watches that flag, so open live-update
streams never close, the worker sits in "Waiting for connections to close" until
gunicorn's ``--timeout`` SIGABRTs it, and every in-flight request dies with it.
"""

from __future__ import annotations

import sys

from gunicorn.arbiter import Arbiter
from uvicorn.server import Server
from uvicorn.workers import UvicornWorker


class _RecyclingServer(Server):
    async def on_tick(self, counter: int) -> bool:
        should_exit = await super().on_tick(counter)
        if should_exit:
            self.should_exit = True
        return should_exit


class RecyclingUvicornWorker(UvicornWorker):
    # Mirrors UvicornWorker._serve with the server class swapped.
    async def _serve(self) -> None:
        self.config.app = self.wsgi
        server = _RecyclingServer(config=self.config)
        self._install_sigquit_handler()
        await server.serve(sockets=self.sockets)
        if not server.started:
            sys.exit(Arbiter.WORKER_BOOT_ERROR)
