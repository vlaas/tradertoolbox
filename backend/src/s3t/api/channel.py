"""The messages of one browser connection, whatever carries them (API-03, API-04, API-08).

A :class:`Connection` reads the client's messages and runs one task per subscription. It knows a
:class:`Carrier`, which moves whole messages, and a :class:`Codec`, which makes them; it does not
know that the carrier is a WebSocket (``s3t.api.ws``). A test of the repository holds that nothing
of the web framework is imported here.

Decided 2026-11-27 (owner, WP-0.7 review):

- **Two codecs, the same messages:** Protocol Buffers, and their canonical JSON for people. The
  client chooses when it connects.
- **A subscription has a number** that the client chooses. The numbers rise and are used once, so a
  late message of one subscription is never taken for another's. Unknown fields are ignored, as
  Protocol Buffers do.
- **A slow client gets fewer messages, not late ones.** A topic sends the state as it is when the
  connection is free again; nothing is queued. A client that takes nothing for a while is given up.
- **A topic that fails** ends its subscription with a message and is logged. The others go on.
- **What cannot be read** ends the connection, because nothing after it can be trusted.
- **A heartbeat** goes out every few seconds, so that a client can tell silence from a lost
  connection. A browser is not told when its network has gone.

Nothing of a connection stays when it ends: a client that comes back says what it wants and gets
snapshots.
"""

import asyncio
import contextlib
import itertools
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import datetime
from typing import Any, Protocol

from google.protobuf import json_format
from google.protobuf.message import DecodeError

from s3t.api.rows import micros
from s3t.broker.limits import Sleep
from s3t.system.features import Feature
from s3t.wire.v1.channel_pb2 import (
    BYE_KIND_PROTOCOL,
    BYE_KIND_SESSION_ENDED,
    REFUSAL_KIND_FAILED,
    REFUSAL_KIND_INVALID,
    REFUSAL_KIND_UNAVAILABLE,
    REFUSAL_KIND_UNKNOWN_TOPIC,
    Bye,
    ByeKind,
    ClientMessage,
    Heartbeat,
    RefusalKind,
    Refused,
    ServerMessage,
    Subscribe,
)

HEARTBEAT_S = 5.0
SEND_TIMEOUT_S = 30.0
BYE_TIMEOUT_S = 2.0
MOST_SUBSCRIPTIONS = 64

LOG = logging.getLogger(__name__)

type Data = bytes | str
"""One whole message: bytes are Protocol Buffers, text is JSON."""


class CarrierClosed(Exception):
    """The other side is gone, or takes nothing any more."""


class Carrier(Protocol):
    async def receive(self) -> Data:
        """The next message. Raises :class:`CarrierClosed`."""
        ...

    async def send(self, data: Data) -> None:
        """Hand over a message; waits while the other side is behind. Raises
        :class:`CarrierClosed`."""
        ...


class Unreadable(Exception):
    """A message of the client cannot be read."""


class Codec(Protocol):
    def encode(self, message: ServerMessage) -> Data: ...

    def decode(self, data: Data) -> ClientMessage:
        """Raises :class:`Unreadable`."""
        ...


class BinaryCodec:
    """Protocol Buffers: normal use."""

    def encode(self, message: ServerMessage) -> Data:
        return message.SerializeToString()

    def decode(self, data: Data) -> ClientMessage:
        if isinstance(data, str):
            raise Unreadable("text where bytes belong")
        try:
            return ClientMessage.FromString(data)
        except DecodeError:
            raise Unreadable("not a message") from None


class JsonCodec:
    """Canonical proto3 JSON of the same messages, for people (API-04)."""

    def encode(self, message: ServerMessage) -> Data:
        return json_format.MessageToJson(message, indent=None)

    def decode(self, data: Data) -> ClientMessage:
        if isinstance(data, bytes):
            raise Unreadable("bytes where text belongs")
        try:
            return json_format.Parse(data, ClientMessage(), ignore_unknown_fields=True)
        except (json_format.ParseError, RecursionError):
            raise Unreadable("not a message") from None


# ---------------------------------------------------------------------- topics

type Emit = Callable[[ServerMessage], Awaitable[None]]
"""Sends a message of the subscription; its number is filled in."""


class TopicRefused(Exception):
    """The subscription cannot be served."""

    def __init__(self, kind: RefusalKind, reason: str) -> None:
        super().__init__(reason)
        self.kind, self.reason = kind, reason


class Topic(Protocol):
    @property
    def feature(self) -> Feature | None:
        """The feature the topic serves (GEN-04); None for one of the process itself."""
        ...

    async def run(self, request: Subscribe, emit: Emit, name: str) -> None:
        """Serve one subscription until it is cancelled. ``name`` is its own in the process.
        Raises :class:`TopicRefused`."""
        ...


class Work:
    """What goes on beside the sending of a topic. Each piece wakes the sending when it has
    something, and one that fails ends it: a topic must not fall silent."""

    def __init__(self) -> None:
        self._ready = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._failure: BaseException | None = None

    def start(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.append(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (failure := task.exception()) is not None:
            self._failure = failure
            self.wake()

    def wake(self) -> None:
        self._ready.set()

    async def wait(self) -> None:
        """Until something woke it."""
        await self._ready.wait()
        self._ready.clear()
        if self._failure is not None:
            raise self._failure

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


# ---------------------------------------------------------------------- the connection


class Connection:
    def __init__(
        self,
        carrier: Carrier,
        codec: Codec,
        topics: Mapping[str, Topic],
        *,
        number: int,
        data_time: Callable[[], datetime],
        refusal: Callable[[Feature], Awaitable[str | None]],
        sleep: Sleep = asyncio.sleep,
        send_timeout: float = SEND_TIMEOUT_S,
    ) -> None:
        self._carrier, self._codec, self._topics = carrier, codec, topics
        self._number, self._data_time, self._refusal = number, data_time, refusal
        self._sleep, self._send_timeout = sleep, send_timeout
        self._sending = asyncio.Lock()
        self._ended = asyncio.Event()
        self._bye: Bye | None = None
        self._last_id = 0
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def __repr__(self) -> str:
        return f"Connection({self._number}, {len(self._tasks)} subscriptions)"

    async def run(self) -> None:
        """Until the carrier is closed or the connection is ended. Raises nothing."""
        parts = [
            asyncio.create_task(self._read()),
            asyncio.create_task(self._beat()),
            asyncio.create_task(self._until_ended()),
        ]
        try:
            await asyncio.wait(parts, return_when=asyncio.FIRST_COMPLETED)
        finally:
            tasks = [*parts, *self._tasks.values()]
            for task in tasks:
                task.cancel()
            for result in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(result, Exception) and not isinstance(result, CarrierClosed):
                    LOG.error("connection %d failed", self._number, exc_info=result)
        if self._bye is not None:
            with contextlib.suppress(CarrierClosed):
                await self._send(ServerMessage(bye=self._bye), BYE_TIMEOUT_S)

    def end(self, kind: ByeKind, reason: str) -> None:
        """End the connection, with a word to the client. Returns at once."""
        if self._bye is None:
            self._bye = Bye(kind=kind, reason=reason)
        self._ended.set()

    async def _until_ended(self) -> None:
        await self._ended.wait()

    async def _send(self, message: ServerMessage, within: float) -> None:
        data = self._codec.encode(message)
        async with self._sending:  # one message at a time, in order
            try:
                async with asyncio.timeout(within):
                    await self._carrier.send(data)
            except TimeoutError:
                # Closing would wait for the same thing as the sending did.
                raise CarrierClosed("the client takes nothing") from None

    async def _beat(self) -> None:
        while True:
            await self._sleep(HEARTBEAT_S)
            beat = Heartbeat(data_time_us=micros(self._data_time()))
            await self._send(ServerMessage(heartbeat=beat), self._send_timeout)

    # ------------------------------------------------------------------ the client's messages

    async def _read(self) -> None:
        while not self._ended.is_set():
            data = await self._carrier.receive()
            try:
                message = self._codec.decode(data)
            except Unreadable as e:
                self._protocol_error(str(e))
                return
            body = message.WhichOneof("body")
            if body == "subscribe":
                self._open(message.subscribe)
            elif body == "unsubscribe":
                self._close(message.unsubscribe.id)
            # Anything else is of a newer client; a known message is never without a body.

    def _protocol_error(self, reason: str) -> None:
        LOG.warning("connection %d: %s", self._number, reason)
        self.end(BYE_KIND_PROTOCOL, reason)

    def _open(self, request: Subscribe) -> None:
        if request.id <= self._last_id:
            self._protocol_error("the numbers of the subscriptions do not rise")
            return
        self._last_id = request.id
        full = len(self._tasks) >= MOST_SUBSCRIPTIONS
        task = asyncio.create_task(self._serve(request, full), name=f"subscription-{request.id}")
        self._tasks[request.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(request.id, None))

    def _close(self, number: int) -> None:
        task = self._tasks.get(number)  # it may have ended by itself
        if task is not None:
            task.cancel()

    # ------------------------------------------------------------------ one subscription

    async def _serve(self, request: Subscribe, full: bool) -> None:
        async def emit(message: ServerMessage) -> None:
            message.id = request.id
            await self._send(message, self._send_timeout)

        try:
            try:
                if full:
                    reason = f"a connection has at most {MOST_SUBSCRIPTIONS} subscriptions"
                    raise TopicRefused(REFUSAL_KIND_INVALID, reason)
                topic = await self._topic(request)
                await topic.run(request, emit, f"{self._number}:{request.id}")
                raise TopicRefused(REFUSAL_KIND_FAILED, "the topic has ended")
            except TopicRefused as e:
                await emit(ServerMessage(refused=Refused(kind=e.kind, reason=e.reason)))
            except CarrierClosed:
                raise
            except Exception:
                LOG.exception("subscription %d:%d failed", self._number, request.id)
                reason = "the topic failed; the log of the backend has the error"
                await emit(ServerMessage(refused=Refused(kind=REFUSAL_KIND_FAILED, reason=reason)))
        except CarrierClosed:
            self._ended.set()

    async def _topic(self, request: Subscribe) -> Topic:
        topic = self._topics.get(request.WhichOneof("topic") or "")
        if topic is None:
            raise TopicRefused(REFUSAL_KIND_UNKNOWN_TOPIC, "the backend does not know the topic")
        if topic.feature is not None:
            why = await self._refusal(topic.feature)
            if why is not None:
                raise TopicRefused(REFUSAL_KIND_UNAVAILABLE, why)
        return topic


class Connections:
    """The open connections, by the login session that opened them."""

    def __init__(self) -> None:
        self._open: dict[str, set[Connection]] = {}
        self._numbers = itertools.count(1)

    def __len__(self) -> int:
        return sum(map(len, self._open.values()))

    def next_number(self) -> int:
        """A number for a connection, its own in the process."""
        return next(self._numbers)

    def add(self, session_id: str, connection: Connection) -> None:
        self._open.setdefault(session_id, set()).add(connection)

    def remove(self, session_id: str, connection: Connection) -> None:
        held = self._open.get(session_id, set())
        held.discard(connection)
        if not held:
            self._open.pop(session_id, None)

    def end(self, session_id: str) -> None:
        """The login session has ended. Returns at once."""
        for connection in tuple(self._open.get(session_id, ())):
            connection.end(BYE_KIND_SESSION_ENDED, "the login session has ended")
