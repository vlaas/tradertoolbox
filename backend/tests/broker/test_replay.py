"""Recording a session and replaying it (BRK-05; plan 3.2).

The recording here is made from the fake broker into a temporary directory: ``make check``
uses no real market data. What the replay broker serves must equal what the application saw
while it was recorded. The test plays the part of the subscription manager: it sends its
pairs when a session says ``connected``, and again after the recorded drop."""

import asyncio
import json
import math
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from backports import zstd

from s3t.broker.fake.broker import FakeBroker
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.interface import (
    Broker,
    BrokerError,
    BrokerProtocolError,
    CandlePeriod,
    EventBatch,
    EventType,
    Gap,
    NotFound,
    Stream,
    StreamItem,
    StreamRefused,
    StreamState,
    Sub,
)
from s3t.broker.recording import META, REST, STREAM, RecordingBroker, RecordingWriter
from s3t.broker.replay import ReplayBroker, read_lines

from .conftest import ASOF

pytestmark = pytest.mark.anyio

SUBS = [
    Sub("SPY", EventType.QUOTE),
    Sub("SPY", EventType.TRADE),
    Sub("SPX", EventType.TRADE),
    Sub(".SPY261127C600", EventType.QUOTE),
    Sub(".SPY261127C600", EventType.GREEKS),
    Sub("SPY", EventType.CANDLE, CandlePeriod(1, "minute"), ASOF - timedelta(minutes=3)),
]

type Recorded = tuple[Path, list[StreamItem], dict[str, Any]]


def same(a: Any, b: Any) -> bool:
    """Equal, with NaN equal to NaN."""
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    return a == b


def plain(item: StreamItem, without: str | None = None) -> tuple[Any, ...] | None:
    """An item without its sequence number; a batch without the rows of one symbol. NaN is
    written as None, so that items compare equal."""
    if isinstance(item, EventBatch):
        rows = [row for row in zip(*item.columns, strict=True) if row[0] != without]
        if not rows:
            return None
        values = [[None if x != x else x for x in row] for row in rows]
        return ("batch", item.event, values, item.received_at)
    if isinstance(item, StreamState):
        return None if item.phase in ("idle", "closed") else ("state", item.phase, item.at)
    if isinstance(item, Gap):
        return ("gap", item.since, item.until, item.reason)
    return ("notice", item.at, item.kind, item.message, item.event)


async def until_connected(stream: Stream, clock: VirtualClock) -> None:
    for _ in range(20):
        if stream.status().phase == "connected":
            return
        await clock.settle()
    raise AssertionError(f"the stream is {stream.status().phase}")


@pytest.fixture
async def recorded(tmp_path: Path) -> Recorded:
    """A session of the fake broker with a drop in it, recorded. Returns the directory, the
    items the application saw and the documents it read."""
    clock = VirtualClock(ASOF)
    # A burst of 20: the reads below must not wait on the limiter, the clock stands still.
    fake = FakeBroker(clock=clock.monotonic, sleep=clock.sleep, now=clock.wall, burst=20)
    directory = tmp_path / "recordings" / "fake-session"
    broker = RecordingBroker(fake, directory)
    assert json.loads((directory / META).read_text())["complete"] is False
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
    await until_connected(stream, clock)
    await stream.subscribe(SUBS)
    ticker = asyncio.create_task(fake.dxlink.run(clock.sleep))
    await clock.advance(5)
    fake.dxlink.drop()
    await clock.advance(5)
    await stream.subscribe(SUBS)  # the new session is empty: the pairs are sent again
    await clock.advance(3)
    await stream.unsubscribe([SUBS[2]])
    await clock.advance(3)
    ticker.cancel()
    await broker.aclose()
    await broker.aclose()  # closing twice is harmless
    return directory, seen, docs


async def test_the_recording_holds_no_secret_and_no_broker_key(recorded: Recorded) -> None:
    directory, seen, _ = recorded
    meta = json.loads((directory / META).read_text())
    assert meta["format"] == 2 and meta["source"] == "fake" and meta["lost"] == 0
    assert meta["recorded_on"] == "2026-11-20" and meta["name"] == "fake-session"
    assert meta["items"] > len(seen) and meta["complete"] is True
    assert (directory / STREAM).name == "stream.jsonl.zst"
    lines = list(read_lines(directory / STREAM))
    stream_text = "\n".join(json.dumps(line) for line in lines)
    text = (directory / META).read_text() + (directory / REST).read_text() + stream_text
    assert "fake-dxlink-token" not in text and "fake-refresh-token" not in text
    assert "quote_token" not in text and "Bearer" not in text
    # The instruments and chains are the application's records too (GEN-09).
    rest = (directory / REST).read_text()
    for word in ("streamer-symbol", "strike-price", "is-etf", "shares-per-contract"):
        assert word not in rest, word
    # The stream file holds the application's records: no dxFeed name, no NaN as text.
    for word in ("eventSymbol", "bidPrice", "eventFlags", "NaN", "{=m}", '"Quote"'):
        assert word not in stream_text, word
    names = {line["e"]: line["f"] for line in lines if line["k"] == "f"}
    assert names["quote"] == [
        "symbol",
        "bid",
        "ask",
        "bid_size",
        "ask_size",
        "bid_time",
        "ask_time",
    ]
    index_trade = next(
        line
        for line in lines
        if line["k"] == "b" and line["e"] == "trade" and "SPX" in line["c"][0]
    )
    row = index_trade["c"][0].index("SPX")
    assert index_trade["c"][2][row] is None  # the size of an index trade: missing, so null
    candle = next(line for line in lines if line["k"] == "b" and line["e"] == "candle")
    assert set(candle["c"][1]) == {"1m"} and isinstance(candle["c"][2][0], int)
    calls = [line for line in lines if line["k"] in "+-"]
    assert [(c["k"], len(c["subs"])) for c in calls] == [("+", 6), ("+", 6), ("-", 1)]
    assert calls[0]["subs"][5][:3] == ["SPY", "candle", "1m"]
    with pytest.raises(FileExistsError):
        RecordingWriter(directory)


async def test_replay_gives_what_was_seen(recorded: Recorded) -> None:
    directory, seen, docs = recorded
    clock = VirtualClock(ASOF)
    broker = ReplayBroker(directory, sleep=clock.sleep, paused=True)
    typed: Broker = broker
    assert typed.source().mark == "REPLAY 2026-11-20"
    assert broker.source().recorded_on == date(2026, 11, 20) and broker.data_time() == ASOF
    assert await broker.equity("SPY") == docs["equity"]
    assert await broker.option_chain("AAPL") == docs["chain"]
    assert await broker.market_metrics(["SPY", "SPX"]) == docs["metrics"]
    for missing in (broker.equity("NOSUCH"), broker.equity("AAPL"), broker.future("/ESZ6")):
        with pytest.raises(NotFound, match="not in the recording"):
            await missing
    assert (await broker.quote_token()).token.get_secret_value() == "replay"

    wanted = [s for s in SUBS if s != SUBS[2]]  # without the SPX trades
    items: list[StreamItem] = []
    fills: list[asyncio.Task[None]] = []

    def sink(item: StreamItem) -> None:
        items.append(item)
        if isinstance(item, StreamState) and item.phase == "connected":
            fills.append(asyncio.ensure_future(stream.subscribe(wanted)))

    stream = broker.stream(sink)
    with pytest.raises(StreamRefused):
        await stream.subscribe(wanted)  # refused while the replayed session is not connected
    stream.start()
    await until_connected(stream, clock)
    assert stream.status().subscribed == 5 and not broker.resumed.is_set()
    broker.resume()
    await clock.advance(30)
    assert stream.status().phase == "disconnected"
    assert stream.status().reason == "end of the recording"

    expected = [p for p in (plain(i, without="SPX") for i in seen) if p is not None]
    replayed = [p for p in (plain(i) for i in items[:-1]) if p is not None]
    cut = next(n for n, p in enumerate(expected) if p[0] == "gap")
    # The first session, the drop and the gap come out as they were seen.
    assert replayed[: cut + 1] == expected[: cut + 1]
    # After the recorded reconnect the stream is empty. The pairs are sent again at once,
    # earlier than in the recording, so each gets its last recorded event first. Everything
    # that was seen after it follows, in order.
    rest = iter(replayed[cut + 1 :])
    assert all(p in rest for p in expected[cut + 1 :])
    assert len(fills) == 2 and all(f.done() and f.exception() is None for f in fills)

    assert [i.seq for i in items] == list(range(1, len(items) + 1))
    phases = [i.phase for i in items if isinstance(i, StreamState)]
    assert phases.count("connected") == 2 and "disconnected" in phases
    (gap,) = [i for i in items if isinstance(i, Gap)]
    before = [i for i in items if isinstance(i, EventBatch) and i.seq < gap.seq]
    assert gap.last_seq == before[-1].seq
    status = stream.status()
    assert (status.subscribed, status.candles, status.reconnects) == (5, 1, 1)
    assert broker.data_time() >= ASOF + timedelta(seconds=15)
    with pytest.raises(BrokerError, match="one streaming session"):
        broker.stream(items.append)
    await broker.aclose()
    assert stream.status().phase == "closed"


async def test_replay_keeps_the_recorded_pace(recorded: Recorded) -> None:
    directory, seen, _ = recorded
    clock = VirtualClock(ASOF)
    broker = ReplayBroker(directory, speed=2.0, sleep=clock.sleep, paused=True)
    items: list[StreamItem] = []
    stream = broker.stream(items.append)
    stream.start()
    await until_connected(stream, clock)
    await stream.subscribe([SUBS[1]])
    broker.resume()
    await clock.advance(2.0)  # two seconds at double speed: four recorded seconds
    assert broker.data_time() == ASOF + timedelta(seconds=4)
    trades = [i for i in items if isinstance(i, EventBatch)]
    original = [i for i in seen if isinstance(i, EventBatch) and i.event is EventType.TRADE]
    assert [t.received_at for t in trades] == [
        o.received_at for o in original if o.received_at <= ASOF + timedelta(seconds=4)
    ]
    # A pair subscribed late gets its last recorded event at once.
    await stream.subscribe([SUBS[0], Sub("NOSUCH", EventType.QUOTE)])
    late = items[-1]
    assert isinstance(late, EventBatch) and late.event is EventType.QUOTE
    assert next(late.rows()).symbol == "SPY" and late.received_at == broker.data_time()
    await stream.unsubscribe([SUBS[1]])
    count = len(items)
    await clock.advance(1.0)
    assert all(i.event is EventType.QUOTE for i in items[count:] if isinstance(i, EventBatch))
    await broker.aclose()


async def test_at_full_speed_the_replay_does_not_wait(recorded: Recorded) -> None:
    directory, seen, _ = recorded
    broker = ReplayBroker(directory, speed=None)
    items: list[StreamItem] = []
    stream = broker.stream(items.append)
    stream.start()
    for _ in range(200):
        await asyncio.sleep(0)
        if stream.status().reason == "end of the recording":
            break
    # Nothing was subscribed, so no batch came; the states and the gap did.
    assert stream.status().reason == "end of the recording"
    assert not any(isinstance(i, EventBatch) for i in items)
    assert len([i for i in items if isinstance(i, Gap)]) == 1
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


async def test_a_recording_that_was_cut_off_is_readable_up_to_the_cut(
    recorded: Recorded, tmp_path: Path
) -> None:
    directory, _, _ = recorded
    whole = list(read_lines(directory / STREAM))
    data = (directory / STREAM).read_bytes()
    assert zstd.get_frame_size(data) == len(data)  # one frame for the whole recording
    cut = tmp_path / "cut.zst"
    for keep in (len(data) - 7, len(data) // 2, 10):
        cut.write_bytes(data[:keep])
        got = list(read_lines(cut))
        assert len(got) < len(whole) and got == whole[: len(got)], keep
    assert len(list(read_lines(cut))) == 0  # ten bytes hold no line
    cut.write_bytes(data + data[: len(data) // 2])  # a whole frame, then a broken one
    got = list(read_lines(cut))
    assert got[: len(whole)] == whole and got[len(whole) :] == whole[: len(got) - len(whole)]
    cut.write_bytes(data[: len(data) // 2] + b"not zstd at all" * 10)
    got = list(read_lines(cut))
    assert got == whole[: len(got)]  # ends without an error where the data is broken
    cut.write_bytes(b"")
    assert list(read_lines(cut)) == []


def test_the_writer_flushes_while_it_runs(tmp_path: Path) -> None:
    writer = RecordingWriter(tmp_path / "live", {"name": "live"}, flush_seconds=0.05)
    for n in range(3):
        writer.put(STREAM, {"k": "n", "t": n, "kind": "error", "m": "x", "e": None})
    lines: list[dict[str, Any]] = []
    for _ in range(100):
        time.sleep(0.02)
        lines = list(read_lines(tmp_path / "live" / STREAM))
        if len(lines) == 3:
            break
    # Readable before it is closed, as after a process that was killed.
    assert [line["t"] for line in lines] == [0, 1, 2]
    meta = json.loads((tmp_path / "live" / META).read_text())
    assert meta == {"name": "live", "format": 2, "complete": False}
    time.sleep(0.2)  # idle: no flush without new lines
    writer.put(STREAM, {"k": "n", "t": 3, "kind": "error", "m": "x", "e": None})
    writer.close({"ended_at": "later"})
    assert len(list(read_lines(tmp_path / "live" / STREAM))) == 4
    data = (tmp_path / "live" / STREAM).read_bytes()
    assert zstd.get_frame_size(data) == len(data)  # flushed blocks, still one frame
    meta = json.loads((tmp_path / "live" / META).read_text())
    assert meta["complete"] is True and meta["items"] == 4 and meta["ended_at"] == "later"


def test_an_item_that_cannot_be_written_does_not_end_the_recording(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writer = RecordingWriter(tmp_path / "odd", flush_seconds=0.05)
    good = {"k": "n", "t": 1, "kind": "error", "m": "x", "e": None}
    writer.put(STREAM, {"k": "n", "t": object()})
    writer.put(REST, {"call": "equity", "args": ["SPY"], "result": object()})
    writer.put(STREAM, good)
    writer.close({})
    assert list(read_lines(tmp_path / "odd" / STREAM)) == [{"k": "x", "lost": 2}, good]
    assert (tmp_path / "odd" / REST).read_text() == ""
    meta = json.loads((tmp_path / "odd" / META).read_text())
    assert (meta["items"], meta["lost"], meta["complete"]) == (1, 2, True)
    assert "could not be encoded" in caplog.text


def test_a_writer_that_failed_does_not_say_complete(tmp_path: Path) -> None:
    class Broken(RecordingWriter):
        def _write_all(self) -> None:
            raise OSError("no space left on device")

    writer = Broken(tmp_path / "broken", queue_size=2)
    for n in range(5):
        writer.put(STREAM, {"k": "n", "t": n, "kind": "error", "m": "x"})
    writer.close({})  # returns although the queue is full and nothing empties it
    assert writer.broken
    assert json.loads((tmp_path / "broken" / META).read_text())["complete"] is False


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
    lines = list(read_lines(tmp_path / "slow" / STREAM))
    assert [x for x in lines if x["k"] == "x"] == [{"k": "x", "lost": 7}]
    assert json.loads((tmp_path / "slow" / META).read_text())["lost"] == 7
