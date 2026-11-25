"""Fixtures for the API tests: the whole backend on the fake broker and a virtual clock, reached
over ASGI inside the test's own event loop, and a WebSocket client for that."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from google.protobuf import json_format
from starlette.types import ASGIApp, Message

from s3t.api.app import create_app
from s3t.broker.fake.broker import FakeBroker
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.interface import Broker
from s3t.config import Config, load_config
from s3t.system.backend import Backend, Time, open_backend
from s3t.system.login import set_password
from s3t.wire.v1.channel_pb2 import ClientMessage, ServerMessage, Subscribe, Unsubscribe
from system.conftest import CHEAP, PASSWORD, anyio_backend  # noqa: F401

ASOF = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)  # a Friday, 10:00 in New York
HOST = "127.0.0.1:8740"
ORIGIN = f"http://{HOST}"
COOKIE = "s3t-8740-session-token"

CONFIG = """
[server]
listen_address = "127.0.0.1"
port = 8740
host_name = "trader.example.internal"
[paths]
env_file = "broker.env"
data_dir = "data"
state_file = "data/state.sqlite"
frontend_dir = "dist"
[store]
daily_copies = 0
[broker]
account_nickname = "main"
"""


def make_config(tmp_path: Path, more: str = 'source = "fake"\n') -> Config:
    """``more`` continues the section of the broker."""
    file = tmp_path / "s3t.toml"
    file.write_text(CONFIG + more)
    return load_config(file)


class Refused(Exception):
    """The WebSocket was not opened."""


class Socket:
    """The client's end of a WebSocket of an ASGI application."""

    def __init__(
        self, app: ASGIApp, *, headers: dict[str, str], subprotocol: str, path: str = "/api/ws"
    ) -> None:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        scope = {
            "type": "websocket",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "scheme": "ws",
            "server": ("127.0.0.1", 8740),
            "client": ("127.0.0.1", 50000),
            "headers": [(b"host", HOST.encode()), *raw],
            "subprotocols": [subprotocol] if subprotocol else [],
            "state": {},
            "extensions": {},
        }
        self.subprotocol = subprotocol
        self.taking = asyncio.Event()
        """Clear it and the client takes nothing more."""
        self.taking.set()
        self.received: list[ServerMessage] = []
        """Every message so far."""
        self.raw: list[bytes | str] = []
        self.closed: int | None = None
        """The close code."""
        self.accepted: Message | None = None
        self._answered = asyncio.Event()
        self._inbox: asyncio.Queue[Message] = asyncio.Queue()
        self._inbox.put_nowait({"type": "websocket.connect"})
        self._id = 0
        self.task = asyncio.create_task(app(scope, self._inbox.get, self._take))  # type: ignore[arg-type]
        self.task.add_done_callback(lambda _: self._answered.set())

    async def _take(self, message: Message) -> None:
        await self.taking.wait()
        kind = message["type"]
        if kind == "websocket.accept":
            self.accepted = message
        elif kind == "websocket.close":
            self.closed = message.get("code", 1000)
        else:
            data = message.get("bytes")
            data = message["text"] if data is None else data
            self.raw.append(data)
            self.received.append(
                ServerMessage.FromString(data)
                if isinstance(data, bytes)
                else json_format.Parse(data, ServerMessage())
            )
        self._answered.set()

    async def open(self) -> "Socket":
        await asyncio.wait_for(self._answered.wait(), 5)
        if self.accepted is None:
            await asyncio.wait_for(self.task, 5)
            raise Refused(str(self.closed))
        return self

    def send(self, message: ClientMessage | bytes | str) -> None:
        if isinstance(message, ClientMessage):
            if self.subprotocol.endswith("json"):
                message = json_format.MessageToJson(message)
            else:
                message = message.SerializeToString()
        key = "bytes" if isinstance(message, bytes) else "text"
        self._inbox.put_nowait({"type": "websocket.receive", key: message})

    def subscribe(self, **topic: Any) -> int:
        """Subscribe under the next number, which is returned: ``status={}``, or
        ``quotes={"symbol": [...]}``."""
        self._id += 1
        self.send(ClientMessage(subscribe=Subscribe(id=self._id, **topic)))
        return self._id

    def unsubscribe(self, number: int) -> None:
        self.send(ClientMessage(unsubscribe=Unsubscribe(id=number)))

    def of(self, number: int) -> list[ServerMessage]:
        """The messages of one subscription, in order."""
        return [m for m in self.received if m.id == number]

    def bodies(self) -> list[str | None]:
        return [m.WhichOneof("body") for m in self.received]

    async def close(self) -> None:
        """The browser goes away."""
        self._inbox.put_nowait({"type": "websocket.disconnect", "code": 1001})
        await asyncio.wait_for(self.task, 5)


@dataclass
class ApiRig:
    backend: Backend
    app: ASGIApp
    clock: VirtualClock
    http: httpx.AsyncClient
    sockets: list[Socket] = field(default_factory=list)

    @property
    def fake(self) -> FakeBroker:
        assert isinstance(self.backend.broker, FakeBroker)
        return self.backend.broker

    async def log_in(self) -> str:
        """Log the client in, and return the token of its session."""
        answer = await self.http.post("/api/login", json={"password": PASSWORD})
        assert answer.status_code == 200, answer.text
        return answer.cookies[COOKIE]

    async def socket(
        self,
        *,
        origin: str | None = ORIGIN,
        token: str | None = None,
        subprotocol: str = "s3t.v1.protobuf",
        path: str = "/api/ws",
    ) -> Socket:
        """An open WebSocket with the session of the client, or of another token."""
        token = self.http.cookies.get(COOKIE) if token is None else token
        headers = {} if origin is None else {"origin": origin}
        if token:
            headers["cookie"] = f"{COOKIE}={token}"
        socket = Socket(self.app, headers=headers, subprotocol=subprotocol, path=path)
        self.sockets.append(socket)
        return await socket.open()

    async def up(self) -> None:
        """The broker's session is connected."""
        for _ in range(20):
            if self.backend.feed.status().stream.phase == "connected":
                return
            await self.clock.settle()
        raise AssertionError(self.backend.feed.status().stream.phase)

    async def pass_time(self, seconds: float) -> None:
        """Let time pass, in steps: what a thread answers comes in between."""
        while seconds > 0:
            step = min(seconds, 0.5)
            await self.clock.advance(step)
            seconds -= step

    async def close(self) -> None:
        for socket in self.sockets:
            if not socket.task.done():
                socket.taking.set()
                await socket.close()


@asynccontextmanager
async def running(
    config: Config,
    clock: VirtualClock,
    *,
    broker: Broker | None = None,
    password: str | None = PASSWORD,
) -> AsyncIterator[ApiRig]:
    """The backend with a password set, and a client that is not logged in. Without a password the
    one in the state file stays."""
    clocks = Time(now=clock.wall, clock=clock.monotonic, sleep=clock.sleep)
    async with open_backend(config, clocks=clocks, broker=broker, hasher=CHEAP) as backend:
        if password is not None:
            await set_password(backend.store, password, hasher=CHEAP, now=clock.wall)
        app = create_app(backend)
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url=ORIGIN, headers={"origin": ORIGIN}
        ) as http:
            rig = ApiRig(backend, app, clock, http)
            try:
                yield rig
            finally:
                await rig.close()


@pytest.fixture
def clock() -> VirtualClock:
    return VirtualClock(ASOF)


@pytest.fixture
async def rig(tmp_path: Path, clock: VirtualClock) -> AsyncIterator[ApiRig]:
    async with running(make_config(tmp_path), clock) as made:
        await made.up()
        yield made


__all__ = ["ApiRig", "Refused", "Socket", "make_config", "running"]
