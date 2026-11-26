"""The web server: one address, one port, and an orderly end (SEC-03, SEC-06).

:func:`listen` opens the one socket the backend listens on. It is opened before the broker and the
stores, and it listens at once, so a second backend on the same address fails there. (Two sockets
that are only bound do not exclude each other on every system.)

uvicorn serves on that socket, inside the application's own event loop and without a lifespan of its
own: what it serves is open before and closed after. Its own signal handling would raise a SIGTERM
again after serving and end the process without closing anything, so the signals are handled here.

A browser that takes nothing more holds up uvicorn's send, its close and its shutdown. The message
layer gives up a send after a while, and the shutdown has a time limit.
"""

import asyncio
import contextlib
import logging
import signal
import socket
from collections.abc import Generator

import uvicorn
from starlette.types import ASGIApp

from s3t.config import ServerConfig

SIGNALS = (signal.SIGINT, signal.SIGTERM)
LARGEST_MESSAGE = 64 * 1024
"""Bytes of one message from the browser. What it sends is small: the topics it wants."""
SHUTDOWN_S = 5

LOG = logging.getLogger(__name__)


def listen(config: ServerConfig) -> socket.socket:
    """The socket, listening on the configured address and on no other. Raises OSError."""
    family = socket.AF_INET6 if ":" in config.listen_address else socket.AF_INET
    return socket.create_server((config.listen_address, config.port), family=family)


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


async def serve(app: ASGIApp, sock: socket.socket, *, stop: asyncio.Event | None = None) -> None:
    """Serve until SIGINT or SIGTERM, or until ``stop`` is set, and return when the connections are
    closed. A second signal does not wait for them. The socket is closed then.

    ``stop`` lets a caller end the server and start another on the same port (the browser suite's
    backend restarts that way; ``tools/e2e_backend.py``)."""
    server = _Server(
        uvicorn.Config(
            app,
            lifespan="off",
            # No configuration: its loggers then reach the handlers of the process, which redact.
            log_config=None,
            proxy_headers=False,
            server_header=False,
            ws="websockets-sansio",
            ws_max_size=LARGEST_MESSAGE,
            timeout_graceful_shutdown=SHUTDOWN_S,
        )
    )
    loop = asyncio.get_running_loop()

    def on_signal(number: signal.Signals) -> None:
        LOG.info("%s: the backend stops", number.name)
        server.force_exit = server.should_exit
        server.should_exit = True

    async def stop_when_set(event: asyncio.Event) -> None:
        await event.wait()
        LOG.info("the backend stops")
        server.should_exit = True

    for number in SIGNALS:
        loop.add_signal_handler(number, on_signal, number)
    watcher = None if stop is None else asyncio.create_task(stop_when_set(stop))
    try:
        await server.serve(sockets=[sock])
    finally:
        for number in SIGNALS:
            loop.remove_signal_handler(number)
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
