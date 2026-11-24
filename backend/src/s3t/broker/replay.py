"""The replay broker: a recorded session served through the broker interface (BRK-05).

It reads a recording made by ``s3t.broker.recording``. It is for work outside market hours
and for performance runs on real data. Its data source says ``replay`` with the date of the
recording, and the app marks every screen "REPLAY <date>" (GEN-11).

How it behaves (decided 2026-11-23):

- **REST reads** return the recorded result of the same call and arguments, the latest if
  it was read more than once. A call that was not recorded raises :class:`NotFound`.
- **The stream** emits the recorded items in their order, with new sequence numbers and the
  recorded receipt times. Of a batch it keeps the events of the pairs that are subscribed
  now. Recorded states, gaps and notices pass through, so a recorded drop is replayed as a
  drop.
- **It follows the session's contract.** A subscribe or unsubscribe call is refused while
  the replayed session is not ``connected``. After a recorded reconnect the stream holds
  nothing, and the subscription manager fills it again, as it does live. There is no hard
  cap on a replay; the status shows 0 for it.
- **Time.** :meth:`ReplayBroker.data_time` is the recorded time at the replay position, so
  time to expiry and the age of a quote come out as they were. With ``speed`` 1.0 the items
  come at the recorded pace, with 10.0 ten times faster, with ``None`` as fast as the event
  loop runs.
- **A pair subscribed late** gets its last recorded event at once, as a live stream sends
  the current value on subscription. For a candle that is its last bar, not the history.
- **Paused start.** With ``paused=True`` the replay stops after the first ``connected``
  until :meth:`ReplayBroker.resume`. A run at full speed sets its demand in that pause;
  without it the events before the first subscription are reduced to the last per pair.
- **At the end** the stream says ``disconnected`` with the reason "end of the recording" and
  stays there. Looping is not built.
- **A recording that was cut off** is read up to the writer's last flush.
"""

import asyncio
import json
import math
from collections.abc import Collection, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from backports import zstd
from pydantic import SecretStr

from s3t.broker.instruments import Future, OptionChain, OptionContract, Security
from s3t.broker.interface import (
    FIELDS,
    BrokerError,
    BrokerProtocolError,
    CandlePeriod,
    DataSource,
    EventBatch,
    EventType,
    Gap,
    JsonObject,
    NotFound,
    QuoteToken,
    RestStatus,
    StreamItem,
    StreamNotice,
    StreamPhase,
    StreamRefused,
    StreamSink,
    StreamState,
    StreamStatus,
    Sub,
)
from s3t.broker.limits import Sleep
from s3t.broker.recording import (
    FORMAT,
    KINDS,
    META,
    REST,
    STREAM,
    ColumnKind,
    decode_result,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
YIELD_EVERY = 200
READ_BYTES = 1 << 20
_ABSENT: dict[ColumnKind, Any] = {
    "text": "",
    "number": math.nan,
    "time": None,
    "period": None,
    "mark": False,
}

type _Key = tuple[EventType, str, CandlePeriod | None]


def from_micros(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def decode_column(kind: ColumnKind, column: Sequence[Any]) -> list[Any]:
    if kind == "number":
        return [math.nan if x is None else float(x) for x in column]
    if kind == "time":
        return [None if x is None else from_micros(x) for x in column]
    if kind == "period":
        return [CandlePeriod.parse(x) for x in column]
    return list(column)


def read_lines(path: Path) -> Iterator[dict[str, Any]]:
    """The lines of a stream file, read as a stream. A file that was cut off ends without
    an error at the last whole line that can be decompressed, which is at or after the
    writer's last flush."""
    decompressor = zstd.ZstdDecompressor()
    pending = b""
    with path.open("rb") as f:
        data = f.read(READ_BYTES)
        while data:
            try:
                pending += decompressor.decompress(data)
            except zstd.ZstdError:
                return
            *lines, pending = pending.split(b"\n")
            for text in lines:
                if text:
                    yield json.loads(text)
            data = b""
            if decompressor.eof:  # a file may hold more than one frame
                data = decompressor.unused_data
                decompressor = zstd.ZstdDecompressor()
            data = data or f.read(READ_BYTES)


class ReplayStream:
    def __init__(self, broker: "ReplayBroker", sink: StreamSink) -> None:
        self._broker, self._sink = broker, sink
        self._subs: set[_Key] = set()
        self._names: dict[EventType, tuple[str, ...]] = {}
        self._last: dict[_Key, tuple[Any, ...]] = {}
        self._seq = 0
        self._last_batch_seq = 0
        self._phase: StreamPhase = "idle"
        self._since: datetime | None = None
        self._reason: str | None = None
        self._last_data_at: datetime | None = None
        self._last_gap: Gap | None = None
        self._reconnects = -1
        self._task: asyncio.Task[None] | None = None
        self.emitted = 0

    def _emit(self, item: StreamItem) -> None:
        self.emitted += 1
        self._sink(item)

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def _state(self, phase: StreamPhase, at: datetime, reason: str | None = None) -> None:
        self._phase, self._since = phase, at
        self._reason = reason if reason is not None else self._reason
        if phase == "connecting":
            self._subs.clear()  # a new session holds nothing
        elif phase == "connected":
            self._reconnects += 1
        self._emit(StreamState(self._next(), phase, at, reason))

    def start(self) -> None:
        if self._task is None and self._phase != "closed":
            self._task = asyncio.create_task(self._run(), name="replay-stream")

    def _must_be_connected(self) -> None:
        if self._phase != "connected":
            raise StreamRefused(
                f"the replayed session is {self._phase}, not connected", reason="not_connected"
            )

    async def subscribe(self, subs: Collection[Sub]) -> None:
        self._must_be_connected()
        late: dict[EventType, list[tuple[Any, ...]]] = {}
        for sub in subs:
            key = (sub.event, sub.symbol, sub.period)
            if key in self._subs:
                continue
            self._subs.add(key)
            row = self._last.get(key)
            if row is not None:
                late.setdefault(sub.event, []).append(row)
        for event, rows in late.items():
            self._batch_out(event, rows, self._broker.data_time())

    async def unsubscribe(self, subs: Collection[Sub]) -> None:
        self._must_be_connected()
        for sub in subs:
            self._subs.discard((sub.event, sub.symbol, sub.period))

    def status(self) -> StreamStatus:
        return StreamStatus(
            phase=self._phase,
            since=self._since,
            reason=self._reason,
            connected_at=self._broker.started_at,
            last_data_at=self._last_data_at,
            reconnects=max(self._reconnects, 0),
            stalls=0,
            last_gap=self._last_gap,
            subscribed=len(self._subs),
            candles=sum(1 for key in self._subs if key[0] is EventType.CANDLE),
            limit=0,
            candle_limit=0,
            aggregation_period=None,
        )

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._phase != "closed":
            self._state("closed", self._broker.data_time())

    async def _run(self) -> None:
        broker = self._broker
        speed, sleep = broker.speed, broker.sleep
        previous: int | None = None
        for n, line in enumerate(broker.stream_lines()):
            kind = line.get("k")
            if kind == "f":
                self._names[EventType(line["e"])] = tuple(line["f"])
                continue
            micros = line.get("t")
            if not isinstance(micros, int):
                continue
            if speed is not None and previous is not None and micros > previous:
                await sleep((micros - previous) / 1_000_000 / speed)
            elif n % YIELD_EVERY == 0:
                await sleep(0)
            previous = micros
            at = from_micros(micros)
            broker.position = at
            if kind == "b":
                self._batch(EventType(line["e"]), line["c"], at)
            elif kind == "s":
                if line["p"] not in ("idle", "closed"):
                    self._state(line["p"], at, line.get("r"))
                    if line["p"] == "connected":
                        await broker.resumed.wait()
            elif kind == "g":
                since = from_micros(line["since"])
                gap = Gap(self._next(), self._last_batch_seq, since, at, line["r"])
                self._last_gap = gap
                self._emit(gap)
            elif kind == "n":
                event = None if line.get("e") is None else EventType(line["e"])
                self._emit(StreamNotice(self._next(), at, line["kind"], line["m"], event))
        self._state("disconnected", broker.data_time(), "end of the recording")

    def _batch(self, event: EventType, recorded: list[list[Any]], at: datetime) -> None:
        names = self._names.get(event)
        if names is None or len(names) != len(recorded):
            return
        by_name = dict(zip(names, recorded, strict=True))
        count = len(recorded[0]) if recorded else 0
        kinds = KINDS[event]
        columns = [
            decode_column(kinds[name], by_name[name])
            if name in by_name
            else [_ABSENT[kinds[name]]] * count
            for name in FIELDS[event]
        ]
        period_at = FIELDS[event].index("period") if event is EventType.CANDLE else None
        kept: list[tuple[Any, ...]] = []
        for row in zip(*columns, strict=True):
            key = (event, row[0], None if period_at is None else row[period_at])
            self._last[key] = row
            if key in self._subs:
                kept.append(row)
        if kept:
            self._batch_out(event, kept, at)

    def _batch_out(self, event: EventType, rows: list[tuple[Any, ...]], at: datetime) -> None:
        columns = tuple(list(column) for column in zip(*rows, strict=True))
        self._last_data_at = at
        self._last_batch_seq = self._next()
        self._emit(EventBatch(self._last_batch_seq, event, at, columns))


class ReplayBroker:
    """The implementation of :class:`s3t.broker.interface.Broker` over a recording."""

    def __init__(
        self,
        directory: Path,
        *,
        speed: float | None = 1.0,
        sleep: Sleep = asyncio.sleep,
        paused: bool = False,
    ) -> None:
        if speed is not None and speed <= 0:
            raise ValueError("speed must be positive, or None for no waiting")
        self.directory = directory
        self.speed, self.sleep = speed, sleep
        self.resumed = asyncio.Event()
        """Clear while a paused replay waits after its first ``connected``."""
        if not paused:
            self.resumed.set()
        try:
            meta = json.loads((directory / META).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise BrokerError(f"no recording named {directory.name}") from None
        except ValueError:
            raise BrokerProtocolError(f"recording {directory.name}: unreadable") from None
        if meta.get("format") != FORMAT:
            raise BrokerProtocolError(
                f"recording {directory.name}: format {meta.get('format')}, this reads {FORMAT}"
            )
        self.meta: dict[str, Any] = meta
        self.started_at = datetime.fromisoformat(meta["started_at"])
        self.position = self.started_at
        """UTC. The recorded time the replay has reached."""
        self._recorded_on = date.fromisoformat(meta["recorded_on"])
        self._rest: dict[tuple[str, str], dict[str, Any]] = {}
        with (directory / REST).open(encoding="utf-8") as f:
            for text in f:
                line = json.loads(text)
                self._rest[(line["call"], json.dumps(line["args"]))] = line
        self._stream: ReplayStream | None = None

    def __repr__(self) -> str:
        return f"ReplayBroker({self.directory.name!r})"

    def stream_lines(self) -> Iterator[dict[str, Any]]:
        return read_lines(self.directory / STREAM)

    def resume(self) -> None:
        """Let a paused replay go on."""
        self.resumed.set()

    def _read(self, call: str, *args: Any) -> Any:
        line = self._rest.get((call, json.dumps(list(args))))
        if line is None or "result" not in line:
            raise NotFound(f"{call}: not in the recording {self.directory.name}", status=404)
        return decode_result(call, line["result"])

    async def quote_token(self) -> QuoteToken:
        at = self.data_time()
        return QuoteToken("replay:", SecretStr("replay"), "replay", at, at + timedelta(days=1))

    async def equity(self, symbol: str) -> Security:
        return self._read("equity", symbol)

    async def equity_option(self, symbol: str) -> OptionContract:
        return self._read("equity_option", symbol)

    async def option_chain(self, underlying: str) -> OptionChain:
        return self._read("option_chain", underlying)

    async def future(self, symbol: str) -> Future:
        return self._read("future", symbol)

    async def future_option(self, symbol: str) -> OptionContract:
        return self._read("future_option", symbol)

    async def futures_option_chain(self, root: str) -> OptionChain:
        return self._read("futures_option_chain", root)

    async def market_metrics(self, symbols: Sequence[str]) -> list[JsonObject]:
        return self._read("market_metrics", list(symbols))

    def rest_status(self) -> RestStatus:
        return RestStatus(
            auth="none",
            token_expires_at=None,
            renews_from=None,
            last_renewal_at=None,
            renewals=0,
            refusal=None,
            rate=0.0,
            burst=0,
            requests_last_minute=0,
            held_until=None,
            rate_limited=0,
        )

    def stream(self, sink: StreamSink) -> ReplayStream:
        if self._stream is not None:
            raise BrokerError("the broker has one streaming session, and it exists already")
        self._stream = ReplayStream(self, sink)
        return self._stream

    def source(self) -> DataSource:
        return DataSource("replay", self._recorded_on)

    def data_time(self) -> datetime:
        return self.position

    async def aclose(self) -> None:
        if self._stream is not None:
            await self._stream.aclose()
