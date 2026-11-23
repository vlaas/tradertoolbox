"""The connection under the DXLink session: text messages over a WebSocket (BRK-02).

The session talks to a :class:`Connection`, not to a library. The real one is the adapter
over ``websockets`` below. The fake broker gives an in-memory one, so the tests need no port
(``s3t.broker.fake.stream``).

Kept from `tt` tt_capture.py 1.6.0 (``DXLinkFeed.start``): no WebSocket ping, because DXLink
has its own keepalive; an own user agent. Changed (2026-11-23, WP-0.4): incoming messages
are limited to 64 MiB instead of unlimited. No ``Origin`` header is sent.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from s3t import __version__

USER_AGENT = f"s3t/{__version__}"
OPEN_TIMEOUT_S = 20.0
CLOSE_TIMEOUT_S = 5.0
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


class ConnectionLost(Exception):
    """The connection is closed or could not be opened. The message holds no secret."""


class Connection(Protocol):
    async def send(self, text: str) -> None:
        """Send one message. Raises :class:`ConnectionLost` when the connection is gone."""
        ...

    async def recv(self) -> str:
        """The next message. Raises :class:`ConnectionLost` when the connection is gone."""
        ...

    async def close(self) -> None: ...


type Connect = Callable[[str], Awaitable[Connection]]
"""Opens a connection to an address, or raises :class:`ConnectionLost`."""


class WebSocketConnection:
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws

    async def send(self, text: str) -> None:
        try:
            await self._ws.send(text)
        except ConnectionClosed as e:
            raise ConnectionLost(_why(e)) from None

    async def recv(self) -> str:
        try:
            message = await self._ws.recv()
        except ConnectionClosed as e:
            raise ConnectionLost(_why(e)) from None
        return message if isinstance(message, str) else message.decode()

    async def close(self) -> None:
        await self._ws.close()


def _why(e: ConnectionClosed) -> str:
    received = e.rcvd
    if received is None:
        return "connection closed without a close frame"
    return f"connection closed by the server: {received.code} {received.reason}".rstrip()


async def open_websocket(url: str) -> Connection:
    """Open the DXLink WebSocket. The address may carry nothing secret: the token goes in
    the AUTH message, not in the address."""
    try:
        ws = await connect(
            url,
            ping_interval=None,
            open_timeout=OPEN_TIMEOUT_S,
            close_timeout=CLOSE_TIMEOUT_S,
            max_size=MAX_MESSAGE_BYTES,
            user_agent_header=USER_AGENT,
            origin=None,
        )
    except (OSError, TimeoutError, InvalidHandshake, InvalidURI) as e:
        raise ConnectionLost(f"cannot connect: {type(e).__name__}") from None
    return WebSocketConnection(ws)
