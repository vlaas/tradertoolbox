"""The WebSocket: the one carrier of the connection (API-03, API-04, API-08).

The guard has checked the origin and the session before anything here runs (SEC-02). The client says
by the subprotocol which codec it speaks: ``s3t.v1.protobuf``, or ``s3t.v1.json`` for reading along.
The version in it is the one of the messages. A page that speaks another is refused, before it is
open, and reloads.

The login session is looked at again while the connection is open. A logout ends the connection at
once, because it happens in this process; a password that was changed by the tool is seen at the
next look.
"""

import asyncio
import contextlib
import logging

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from s3t.api.channel import (
    BYE_TIMEOUT_S,
    BinaryCodec,
    CarrierClosed,
    Codec,
    Connection,
    Connections,
    Data,
    JsonCodec,
)
from s3t.api.guard import PREFIX, session_of
from s3t.api.topics import make_topics
from s3t.system.backend import Backend
from s3t.wire.v1.channel_pb2 import BYE_KIND_SESSION_ENDED

CODECS: dict[str, Codec] = {"s3t.v1.protobuf": BinaryCodec(), "s3t.v1.json": JsonCodec()}
SESSION_LOOK_S = 60.0

LOG = logging.getLogger(__name__)


class WebSocketCarrier:
    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket

    async def receive(self) -> Data:
        try:
            message = await self._ws.receive()
        except (WebSocketDisconnect, RuntimeError, OSError) as e:
            raise CarrierClosed(str(e)) from None
        if message["type"] == "websocket.disconnect":
            raise CarrierClosed("closed by the client")
        data: Data | None = message.get("bytes")
        if data is None:
            data = message.get("text")
        return "" if data is None else data

    async def send(self, data: Data) -> None:
        try:
            if isinstance(data, bytes):
                await self._ws.send_bytes(data)
            else:
                await self._ws.send_text(data)
        # The server raises a RuntimeError for a connection that it has given up itself.
        except (WebSocketDisconnect, RuntimeError, OSError) as e:
            raise CarrierClosed(str(e)) from None


async def websocket(websocket: WebSocket) -> None:
    backend = websocket.app.state.backend
    connections = websocket.app.state.connections
    assert isinstance(backend, Backend) and isinstance(connections, Connections)
    offered: list[str] = websocket.scope.get("subprotocols", [])
    chosen = next((name for name in offered if name in CODECS), None)
    if chosen is None:
        await websocket.close(1002)
        return
    await websocket.accept(subprotocol=chosen)
    session = session_of(websocket)
    connection = Connection(
        WebSocketCarrier(websocket),
        CODECS[chosen],
        make_topics(backend),
        number=connections.next_number(),
        data_time=backend.broker.data_time,
        refusal=backend.features.refusal,
        sleep=backend.time.sleep,
    )
    connections.add(session.id, connection)
    watch = asyncio.create_task(_watch(backend, session.id, connection))
    try:
        await connection.run()
    finally:
        connections.remove(session.id, connection)
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)
    if websocket.client_state is WebSocketState.CONNECTED:
        # A client that takes nothing holds up the close as it does a message.
        with contextlib.suppress(TimeoutError, RuntimeError, WebSocketDisconnect, OSError):
            async with asyncio.timeout(BYE_TIMEOUT_S):
                await websocket.close()


async def _watch(backend: Backend, session_id: str, connection: Connection) -> None:
    while True:
        await backend.time.sleep(SESSION_LOOK_S)
        try:
            held = await backend.login.check(session_id)
        except Exception:
            LOG.exception("the login session could not be looked at")
            held = None
        if held is None:
            connection.end(BYE_KIND_SESSION_ENDED, "the login session has ended")
            return


def install(app: FastAPI) -> None:
    app.add_api_websocket_route(f"{PREFIX}/ws", websocket)
