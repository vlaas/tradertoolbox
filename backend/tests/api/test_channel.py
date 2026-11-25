"""The message layer on a carrier in memory (API-03, API-04, API-08): there is no WebSocket here."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from google.protobuf import json_format

from s3t.api.channel import (
    HEARTBEAT_S,
    MOST_SUBSCRIPTIONS,
    BinaryCodec,
    CarrierClosed,
    Codec,
    Connection,
    Connections,
    Data,
    Emit,
    JsonCodec,
    Topic,
    TopicRefused,
    Unreadable,
    Work,
)
from s3t.broker.fake.clock import VirtualClock
from s3t.system.features import QUOTES, Feature
from s3t.wire.v1.channel_pb2 import (
    BYE_KIND_PROTOCOL,
    BYE_KIND_SESSION_ENDED,
    REFUSAL_KIND_FAILED,
    REFUSAL_KIND_INVALID,
    REFUSAL_KIND_UNAVAILABLE,
    REFUSAL_KIND_UNKNOWN_TOPIC,
    ClientMessage,
    QuotesTopic,
    ServerMessage,
    StatusTopic,
    Subscribe,
    Unsubscribe,
)
from s3t.wire.v1.market_pb2 import QuoteRows

from .conftest import ASOF

pytestmark = pytest.mark.anyio


class Memory:
    """A carrier in memory. ``codec`` is the client's."""

    def __init__(self, codec: Codec) -> None:
        self.codec = codec
        self.inbox: asyncio.Queue[Data | None] = asyncio.Queue()
        self.sent: list[ServerMessage] = []
        self.sending = 0
        self.most_at_once = 0
        self.taking = asyncio.Event()
        self.taking.set()

    async def receive(self) -> Data:
        data = await self.inbox.get()
        if data is None:
            raise CarrierClosed("gone")
        return data

    async def send(self, data: Data) -> None:
        self.sending += 1
        self.most_at_once = max(self.most_at_once, self.sending)
        try:
            await asyncio.sleep(0)
            await self.taking.wait()
        finally:
            self.sending -= 1
        if isinstance(data, bytes):
            self.sent.append(ServerMessage.FromString(data))
        else:
            self.sent.append(json_format.Parse(data, ServerMessage()))

    def put(self, message: ClientMessage | Data | None) -> None:
        if isinstance(message, ClientMessage):
            text = isinstance(self.codec, JsonCodec)
            message = json_format.MessageToJson(message) if text else message.SerializeToString()
        self.inbox.put_nowait(message)

    def subscribe(self, number: int, **topic: Any) -> None:
        self.put(ClientMessage(subscribe=Subscribe(id=number, **topic)))

    def of(self, number: int) -> list[ServerMessage]:
        return [m for m in self.sent if m.id == number]


@dataclass
class Plain:
    """A topic of the test's own: it runs ``body``, or sends what it is told to."""

    feature: Feature | None = None
    body: Callable[[Emit], Awaitable[None]] | None = None
    emits: dict[str, Emit] = field(default_factory=dict)
    """By the name of the subscription."""
    ended: list[str] = field(default_factory=list)

    async def run(self, request: Subscribe, emit: Emit, name: str) -> None:
        try:
            self.emits[name] = emit
            if self.body is not None:
                await self.body(emit)
            await emit(ServerMessage(snapshot=True))
            await asyncio.Event().wait()
        finally:
            self.ended.append(name)


class Rig:
    def __init__(self, clock: VirtualClock, codec: Codec, **options: Any) -> None:
        self.clock = clock
        self.memory = Memory(codec)
        self.topics: dict[str, Any] = {"status": Plain(), "quotes": Plain()}
        self.reasons: dict[str, str] = {}

        async def refusal(feature: Feature) -> str | None:
            return self.reasons.get(feature.name)

        topics: dict[str, Topic] = self.topics
        self.connection = Connection(
            self.memory,
            codec,
            topics,
            number=3,
            data_time=clock.wall,
            refusal=refusal,
            sleep=clock.sleep,
            **options,
        )
        self.run = asyncio.create_task(self.connection.run())

    async def settle(self) -> None:
        await self.clock.settle(20)

    async def done(self) -> None:
        await asyncio.wait_for(asyncio.shield(self.run), 5)


@pytest.fixture
def clock() -> VirtualClock:
    return VirtualClock(ASOF)


@pytest.fixture(params=[BinaryCodec, JsonCodec])
async def rig(request: pytest.FixtureRequest, clock: VirtualClock) -> AsyncIterator[Rig]:
    made = Rig(clock, request.param())
    yield made
    made.memory.taking.set()
    made.memory.put(None)
    await made.done()


# ---------------------------------------------------------------------- the codecs


def test_the_two_codecs_say_the_same() -> None:
    rows = QuoteRows(symbol=["SPY"], bid=[float("nan")], ask=[0.0])
    message = ServerMessage(id=7, snapshot=True)
    message.quotes.rows.CopyFrom(rows)
    binary, text = BinaryCodec().encode(message), JsonCodec().encode(message)
    assert isinstance(binary, bytes) and isinstance(text, str)
    assert ServerMessage.FromString(binary) == json_format.Parse(text, ServerMessage())
    doc = json.loads(text)  # readable, and canonical: names as TypeScript has them
    assert doc["quotes"]["rows"] == {"symbol": ["SPY"], "bid": ["NaN"], "ask": [0.0]}

    request = ClientMessage(subscribe=Subscribe(id=1, quotes=QuotesTopic(symbol=["SPY"])))
    assert BinaryCodec().decode(request.SerializeToString()) == request
    assert JsonCodec().decode(json_format.MessageToJson(request)) == request


@pytest.mark.parametrize("data", [b"\xff\xff\xff", "text", b"\x0a"])
def test_binary_that_cannot_be_read(data: Data) -> None:
    with pytest.raises(Unreadable):
        BinaryCodec().decode(data)


@pytest.mark.parametrize(
    "data",
    ["", "{", "[1]", "null", '{"subscribe": 1}', '{"subscribe": {"id": -1}}', b"{}", "[" * 9999],
)
def test_json_that_cannot_be_read(data: Data) -> None:
    with pytest.raises(Unreadable):
        JsonCodec().decode(data)


# ---------------------------------------------------------------------- subscriptions


async def test_a_subscription_gets_its_snapshot_and_its_changes(rig: Rig) -> None:
    rig.memory.subscribe(1, status=StatusTopic())
    rig.memory.subscribe(5, quotes=QuotesTopic(symbol=["SPY"]))
    await rig.settle()
    assert [(m.id, m.snapshot) for m in rig.memory.sent] == [(1, True), (5, True)]
    await rig.topics["quotes"].emits["3:5"](ServerMessage())
    await rig.topics["status"].emits["3:1"](ServerMessage())
    assert [(m.id, m.snapshot) for m in rig.memory.sent[2:]] == [(5, False), (1, False)]


async def test_an_unsubscription_ends_one(rig: Rig) -> None:
    rig.memory.subscribe(1, status=StatusTopic())
    rig.memory.subscribe(2, status=StatusTopic())
    await rig.settle()
    rig.memory.put(ClientMessage(unsubscribe=Unsubscribe(id=1)))
    rig.memory.put(ClientMessage(unsubscribe=Unsubscribe(id=1)))  # it is gone already
    rig.memory.put(ClientMessage(unsubscribe=Unsubscribe(id=99)))
    await rig.settle()
    assert rig.topics["status"].ended == ["3:1"] and not rig.run.done()
    assert repr(rig.connection) == "Connection(3, 1 subscriptions)"


@pytest.mark.parametrize("numbers", [(0,), (4, 4), (4, 3)])
async def test_a_number_is_used_once(rig: Rig, numbers: tuple[int, ...]) -> None:
    for number in numbers:
        rig.memory.subscribe(number, status=StatusTopic())
    await rig.done()
    assert rig.memory.sent[-1].bye.kind == BYE_KIND_PROTOCOL


async def test_a_topic_of_a_newer_page(rig: Rig) -> None:
    """It is refused, and the connection goes on."""
    rig.memory.subscribe(1)
    rig.memory.put(ClientMessage())  # a message of a kind that is not known here
    rig.memory.subscribe(2, status=StatusTopic())
    await rig.settle()
    assert rig.memory.of(1)[0].refused.kind == REFUSAL_KIND_UNKNOWN_TOPIC
    assert rig.memory.of(2)[0].snapshot


async def test_fields_of_a_newer_page_are_ignored(rig: Rig) -> None:
    if isinstance(rig.memory.codec, JsonCodec):
        rig.memory.put('{"subscribe": {"id": 1, "status": {"detail": 3}}, "later": true}')
    else:
        known = ClientMessage(subscribe=Subscribe(id=1, status=StatusTopic()))
        rig.memory.put(known.SerializeToString() + b"\x98\x06\x01")  # field 99
    await rig.settle()
    assert rig.memory.of(1)[0].snapshot


async def test_a_topic_whose_data_is_absent(rig: Rig) -> None:
    """GEN-04: refused with the reason, at the moment of the subscription."""
    rig.topics["quotes"].feature = QUOTES
    rig.reasons["quotes"] = "the broker refuses its data: scope"
    rig.memory.subscribe(1, quotes=QuotesTopic())
    await rig.settle()
    refused = rig.memory.of(1)[0].refused
    assert refused.kind == REFUSAL_KIND_UNAVAILABLE
    assert refused.reason == "the broker refuses its data: scope"
    assert rig.topics["quotes"].emits == {}
    del rig.reasons["quotes"]
    rig.memory.subscribe(2, quotes=QuotesTopic())
    await rig.settle()
    assert rig.memory.of(2)[0].snapshot


async def test_a_topic_can_refuse(rig: Rig) -> None:
    async def refuse(_: Emit) -> None:
        raise TopicRefused(REFUSAL_KIND_INVALID, "too many")

    rig.topics["quotes"].body = refuse
    rig.memory.subscribe(1, quotes=QuotesTopic())
    await rig.settle()
    assert rig.memory.of(1)[0].refused.reason == "too many"


async def test_a_topic_that_fails_says_so(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    async def fail(_: Emit) -> None:
        raise RuntimeError("a detail of the inside")

    rig.topics["quotes"].body = fail
    rig.memory.subscribe(1, quotes=QuotesTopic())
    rig.memory.subscribe(2, status=StatusTopic())
    await rig.settle()
    refused = rig.memory.of(1)[0].refused
    assert refused.kind == REFUSAL_KIND_FAILED and "detail" not in refused.reason
    assert "a detail of the inside" in caplog.text
    assert rig.memory.of(2)[0].snapshot and not rig.run.done()  # the other one goes on


async def test_a_topic_whose_work_fails_does_not_fall_silent(rig: Rig) -> None:
    async def body(_: Emit) -> None:
        work = Work()

        async def broken() -> None:
            raise RuntimeError("beside")

        work.start(broken())
        try:
            while True:
                await work.wait()
        finally:
            await work.aclose()

    rig.topics["quotes"].body = body
    rig.memory.subscribe(1, quotes=QuotesTopic())
    await rig.settle()
    assert rig.memory.of(1)[0].refused.kind == REFUSAL_KIND_FAILED


async def test_a_page_that_subscribes_without_end(rig: Rig) -> None:
    for number in range(1, MOST_SUBSCRIPTIONS + 3):
        rig.memory.subscribe(number, status=StatusTopic())
    await rig.clock.settle(1000)  # every message takes its turn
    refused = [m.id for m in rig.memory.sent if m.WhichOneof("body") == "refused"]
    assert refused == [MOST_SUBSCRIPTIONS + 1, MOST_SUBSCRIPTIONS + 2]


# ---------------------------------------------------------------------- the connection


@pytest.mark.parametrize("data", [b"\xff\xff\xff", "nonsense"])
async def test_what_cannot_be_read_ends_the_connection(rig: Rig, data: Data) -> None:
    rig.memory.put(data)
    await rig.done()
    assert rig.memory.sent[-1].bye.kind == BYE_KIND_PROTOCOL


async def test_a_heartbeat_when_nothing_else_happens(rig: Rig) -> None:
    await rig.clock.advance(3 * HEARTBEAT_S)
    times = [m.heartbeat.data_time_us for m in rig.memory.sent]
    start = ASOF.timestamp() * 1e6
    assert times == [start + n * HEARTBEAT_S * 1e6 for n in (1, 2, 3)]


async def test_one_message_at_a_time(rig: Rig) -> None:
    for number in range(1, 9):
        rig.memory.subscribe(number, status=StatusTopic())
    await rig.settle()
    assert len(rig.memory.sent) == 8 and rig.memory.most_at_once == 1


async def test_when_the_browser_goes_nothing_stays(rig: Rig) -> None:
    rig.memory.subscribe(1, status=StatusTopic())
    rig.memory.subscribe(2, quotes=QuotesTopic())
    await rig.settle()
    rig.memory.put(None)
    await rig.done()
    assert rig.topics["status"].ended == ["3:1"] and rig.topics["quotes"].ended == ["3:2"]
    assert repr(rig.connection) == "Connection(3, 0 subscriptions)"
    await rig.clock.advance(60)
    assert not [m for m in rig.memory.sent if m.WhichOneof("body") == "heartbeat"]


async def test_the_connection_can_be_ended(rig: Rig) -> None:
    connections = Connections()
    connections.add("a session", rig.connection)
    rig.memory.subscribe(1, status=StatusTopic())
    await rig.settle()
    connections.end("another session")
    await rig.settle()
    assert not rig.run.done() and len(connections) == 1
    connections.end("a session")
    await rig.done()
    assert rig.memory.sent[-1].bye.kind == BYE_KIND_SESSION_ENDED
    assert rig.topics["status"].ended == ["3:1"]
    connections.remove("a session", rig.connection)
    assert len(connections) == 0


async def test_a_client_that_takes_nothing_is_given_up(
    clock: VirtualClock, caplog: pytest.LogCaptureFixture
) -> None:
    """The sending gives up after a while, so nothing of the connection stays, and it ends though
    a close would wait as the sending did."""
    rig = Rig(clock, BinaryCodec(), send_timeout=0.05)
    rig.memory.subscribe(1, status=StatusTopic())
    rig.memory.subscribe(2, quotes=QuotesTopic())
    await rig.settle()
    rig.memory.taking.clear()
    send = rig.topics["quotes"].emits["3:2"]
    with pytest.raises(CarrierClosed):
        await asyncio.wait_for(send(ServerMessage()), 5)
    rig.memory.subscribe(3, status=StatusTopic())  # its snapshot cannot go out either
    await rig.done()
    assert sorted(rig.topics["status"].ended + rig.topics["quotes"].ended) == ["3:1", "3:2", "3:3"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_ending_a_client_that_takes_nothing(
    clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("s3t.api.channel.BYE_TIMEOUT_S", 0.05)
    rig = Rig(clock, BinaryCodec())
    rig.memory.taking.clear()
    rig.connection.end(BYE_KIND_SESSION_ENDED, "over")
    await rig.done()
    assert rig.memory.sent == []
