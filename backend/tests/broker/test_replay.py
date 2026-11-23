"""Recording a session and replaying it (BRK-05; plan 3.2).

The recording here is made from the fake broker into a temporary directory: ``make check``
uses no real market data. What the replay broker serves must equal what the application saw
while it was recorded."""

import asyncio
import gzip
import json
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from s3t.broker.fake.broker import FakeBroker
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.interface import (
    Broker,
    BrokerError,
    BrokerProtocolError,
    EventBatch,
    EventType,
    Gap,
    NotFound,
    StreamItem,
    StreamState,
    Sub,
)
from s3t.broker.recording import META, REST, STREAM, RecordingBroker, RecordingWriter
from s3t.broker.replay import ReplayBroker

from .conftest import ASOF

pytestmark = pytest.mark.anyio

SUBS = [
    Sub("SPY", EventType.QUOTE),
    Sub("SPY", EventType.TRADE),
    Sub("SPX", EventType.TRADE),
    Sub(".SPY261127C600", EventType.QUOTE),
    Sub(".SPY261127C600", EventType.GREEKS),
    Sub("SPY{=m}", EventType.CANDLE, from_time=ASOF - timedelta(minutes=3)),
]


def plain(item: StreamItem) -> tuple[Any, ...]:
    """An item without its sequence number."""
    if isinstance(item, EventBatch):
        return ("batch", item.event, item.fields, list(item.values), item.received_at)
    if isinstance(item, StreamState):
        return ("state", item.phase, item.at, item.reason)
    if isinstance(item, Gap):
        return ("gap", item.since, item.until, item.reason)
    return ("notice", item.at, item.kind, item.message)


@pytest.fixture
async def recorded(tmp_path: Path) -> tuple[Path, list[StreamItem], dict[str, Any]]:
    """A session of the fake broker with a drop in it, recorded. Returns the directory, the
    items the application saw and the documents it read."""
    clock = VirtualClock(ASOF)
    # A burst of 20: the reads below must not wait on the limiter, the clock stands still.
    fake = FakeBroker(clock=clock.monotonic, sleep=clock.sleep, now=clock.wall, burst=20)
    directory = tmp_path / "recordings" / "fake-session"
    broker = RecordingBroker(fake, directory)
    docs = {
        "equity": await broker.equity("SPY"),
        "chain": await broker.option_chain("AAPL"),
        "metrics": await broker.market_metrics(["SPY", "SPX"]),
    }
    with pytest.raises(NotFound):
        await broker.equity("NOSUCH")
    seen: list[StreamItem] = []
    stream = broker.stream(seen.append)
    stream.start()
    stream.subscribe(SUBS)
    ticker = asyncio.create_task(fake.dxlink.run(clock.sleep))
    await clock.advance(5)
    fake.dxlink.drop()
    await clock.advance(5)
    stream.unsubscribe([SUBS[2]])
    await clock.advance(3)
    ticker.cancel()
    await broker.aclose()
    await broker.aclose()  # closing twice is harmless
    return directory, seen, docs


async def test_the_recording_holds_no_secret(
    recorded: tuple[Path, list[StreamItem], dict[str, Any]],
) -> None:
    directory, seen, _ = recorded
    meta = json.loads((directory / META).read_text())
    assert meta["format"] == 1 and meta["source"] == "fake" and meta["lost"] == 0
    assert meta["recorded_on"] == "2026-11-20" and meta["name"] == "fake-session"
    assert meta["items"] > len(seen)
    text = (directory / META).read_text() + (directory / REST).read_text()
    text += gzip.open(directory / STREAM, "rt").read()
    assert "fake-dxlink-token" not in text and "fake-refresh-token" not in text
    assert "quote_token" not in text and "Bearer" not in text
    with pytest.raises(FileExistsError):
        RecordingWriter(directory)


async def test_replay_gives_what_was_seen(
    recorded: tuple[Path, list[StreamItem], dict[str, Any]],
) -> None:
    directory, seen, docs = recorded
    broker = ReplayBroker(directory, speed=None)
    typed: Broker = broker
    assert typed.source().mark == "REPLAY 2026-11-20"
    assert broker.source().recorded_on == date(2026, 11, 20) and broker.now() == ASOF
    assert await broker.equity("SPY") == docs["equity"]
    assert await broker.option_chain("AAPL") == docs["chain"]
    assert await broker.market_metrics(["SPY", "SPX"]) == docs["metrics"]
    for missing in (broker.equity("NOSUCH"), broker.equity("AAPL"), broker.future("/ESZ6")):
        with pytest.raises(NotFound, match="not in the recording"):
            await missing
    assert (await broker.quote_token()).token.get_secret_value() == "replay"

    items: list[StreamItem] = []
    stream = broker.stream(items.append)
    stream.subscribe([s for s in SUBS if s != SUBS[2]])  # without the SPX trades
    stream.start()
    for _ in range(200):
        await asyncio.sleep(0)
        if stream.status().phase == "disconnected":
            break
    assert stream.status().reason == "end of the recording"

    def without_spx(item: StreamItem) -> tuple[Any, ...] | None:
        if isinstance(item, EventBatch):
            rows = [r for r in item.rows() if r["eventSymbol"] != "SPX"]
            if not rows:
                return None
            values = [v for r in rows for v in r.values()]
            return ("batch", item.event, item.fields, values, item.received_at)
        if isinstance(item, StreamState) and item.phase in ("idle", "closed"):
            return None
        return plain(item)

    expected = [p for p in map(without_spx, seen) if p is not None]
    replayed = [plain(i) for i in items[:-1]]
    assert replayed == expected
    assert [i.seq for i in items] == list(range(1, len(items) + 1))
    # The recorded drop is replayed as a drop, with its gap.
    phases = [i.phase for i in items if isinstance(i, StreamState)]
    assert phases.count("connected") == 2 and "disconnected" in phases
    assert len([i for i in items if isinstance(i, Gap)]) == 1
    assert broker.now() == ASOF + timedelta(seconds=13) and stream.status().held == 5
    with pytest.raises(BrokerError, match="one streaming session"):
        broker.stream(items.append)
    await broker.aclose()
    assert stream.status().phase == "closed"


async def test_replay_keeps_the_recorded_pace(
    recorded: tuple[Path, list[StreamItem], dict[str, Any]],
) -> None:
    directory, seen, _ = recorded
    clock = VirtualClock(ASOF)
    broker = ReplayBroker(directory, speed=2.0, sleep=clock.sleep)
    items: list[StreamItem] = []
    stream = broker.stream(items.append)
    stream.subscribe([SUBS[1]])
    stream.start()
    await clock.advance(2.0)  # two seconds at double speed: four recorded seconds
    assert broker.now() == ASOF + timedelta(seconds=4)
    trades = [i for i in items if isinstance(i, EventBatch)]
    original = [i for i in seen if isinstance(i, EventBatch) and i.event is EventType.TRADE]
    assert [t.received_at for t in trades] == [
        o.received_at for o in original if o.received_at <= ASOF + timedelta(seconds=4)
    ]
    # A pair subscribed late gets its last recorded event at once.
    stream.subscribe([SUBS[0], Sub("NOSUCH", EventType.QUOTE)])
    late = items[-1]
    assert isinstance(late, EventBatch) and late.event is EventType.QUOTE
    assert next(late.rows())["eventSymbol"] == "SPY" and late.received_at == broker.now()
    stream.unsubscribe([SUBS[1]])
    count = len(items)
    await clock.advance(1.0)
    assert all(i.event is EventType.QUOTE for i in items[count:] if isinstance(i, EventBatch))
    await broker.aclose()


async def test_a_recording_that_cannot_be_read(tmp_path: Path) -> None:
    with pytest.raises(BrokerError, match="no recording named absent"):
        ReplayBroker(tmp_path / "absent")
    other = tmp_path / "other"
    other.mkdir()
    (other / META).write_text(json.dumps({"format": 99}))
    with pytest.raises(BrokerProtocolError, match="format 99"):
        ReplayBroker(other)
    with pytest.raises(ValueError, match="speed"):
        ReplayBroker(other, speed=0)


def test_a_full_writer_queue_loses_items_and_never_waits(tmp_path: Path) -> None:
    release = threading.Event()

    class Slow(RecordingWriter):
        def _run(self) -> None:
            release.wait(5)
            super()._run()

    writer = Slow(tmp_path / "slow", queue_size=3)
    for n in range(10):
        writer.put(STREAM, {"k": "n", "t": n, "kind": "error", "m": "x"})  # returns at once
    assert writer.lost == 7
    release.set()
    writer.close({"name": "slow"})
    lines = [json.loads(x) for x in gzip.open(tmp_path / "slow" / STREAM, "rt")]
    assert [x for x in lines if x["k"] == "x"] == [{"k": "x", "lost": 7}]
    assert json.loads((tmp_path / "slow" / META).read_text())["lost"] == 7
