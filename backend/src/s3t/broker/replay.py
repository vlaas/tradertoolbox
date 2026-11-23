"""The replay broker: a recorded session served through the broker interface (BRK-05).

It reads a recording made by ``s3t.broker.recording``. It is for work outside market hours
and for performance runs on real data. Its data source says ``replay`` with the date of the
recording, and the app marks every screen "REPLAY <date>" (GEN-11).

How it behaves, proposed 2026-11-23 (WP-0.4):

- **REST reads** return the recorded document of the same call and arguments, the latest if
  it was read more than once. A call that was not recorded raises :class:`NotFound`.
- **The stream** emits the recorded items in their order, with new sequence numbers and the
  recorded receipt times. Of a batch it keeps the events of the pairs that are subscribed
  now. Recorded states, gaps and notices pass through, so a recorded drop is replayed as a
  drop.
- **Time.** :meth:`ReplayBroker.now` is the recorded time at the replay position, so time to
  expiry and the age of a quote come out as they were. With ``speed`` 1.0 the items come at
  the recorded pace, with 10.0 ten times faster, with ``None`` as fast as the reader takes
  them.
- **A pair subscribed late** gets its last recorded event at once, as a live stream sends
  the current value on subscription. For a candle that is its last bar, not the history.
- **At the end** the stream says ``disconnected`` with the reason "end of the recording" and
  stays there. Looping is not built.
"""

import asyncio
import gzip
import json
from collections.abc import Collection, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from s3t.broker.interface import (
    BrokerError,
    BrokerProtocolError,
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
    StreamSink,
    StreamState,
    StreamStatus,
    Sub,
)
from s3t.broker.limits import Sleep
from s3t.broker.recording import FORMAT, META, REST, STREAM

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
YIELD_EVERY = 200


def from_micros(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


class ReplayStream:
    def __init__(self, broker: "ReplayBroker", sink: StreamSink) -> None:
        self._broker, self._sink = broker, sink
        self._subs: set[tuple[str, str]] = set()
        self._fields: dict[EventType, tuple[str, ...]] = {}
        self._last: dict[tuple[str, str], list[Any]] = {}
        self._seq = 0
        self._phase: StreamPhase = "idle"
        self._since: datetime | None = None
        self._reason: str | None = None
        self._last_data_at: datetime | None = None
        self._last_gap: Gap | None = None
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
        self._emit(StreamState(self._next(), phase, at, reason))

    def start(self) -> None:
        if self._task is None and self._phase != "closed":
            self._task = asyncio.create_task(self._run(), name="replay-stream")

    def subscribe(self, subs: Collection[Sub]) -> None:
        late: dict[EventType, list[Any]] = {}
        for sub in subs:
            key = (sub.event.value, sub.symbol)
            if key in self._subs:
                continue
            self._subs.add(key)
            row = self._last.get(key)
            if row is not None:
                late.setdefault(sub.event, []).extend(row)
        for event, values in late.items():
            at = self._broker.now()
            self._emit(EventBatch(self._next(), event, self._fields[event], values, at))

    def unsubscribe(self, subs: Collection[Sub]) -> None:
        for sub in subs:
            self._subs.discard((sub.event.value, sub.symbol))

    async def synced(self) -> None:
        return None

    def status(self) -> StreamStatus:
        return StreamStatus(
            phase=self._phase,
            since=self._since,
            reason=self._reason,
            connected_at=self._broker.started_at,
            last_data_at=self._last_data_at,
            reconnects=0,
            stalls=0,
            last_gap=self._last_gap,
            held=len(self._subs),
            pending=0,
            changes_last_minute=0,
            change_limit=0,
            rate_errors=0,
            aggregation_period=None,
        )

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._phase != "closed":
            self._state("closed", self._broker.now())

    async def _run(self) -> None:
        broker = self._broker
        speed, sleep = broker.speed, broker.sleep
        previous: int | None = None
        for n, line in enumerate(broker.stream_lines()):
            kind = line.get("k")
            if kind == "f":
                self._fields[EventType(line["e"])] = tuple(line["f"])
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
                self._batch(EventType(line["e"]), line["v"], at)
            elif kind == "s":
                if line["p"] not in ("idle", "closed"):
                    self._state(line["p"], at, line.get("r"))
            elif kind == "g":
                gap = Gap(self._next(), self._seq - 1, from_micros(line["since"]), at, line["r"])
                self._last_gap = gap
                self._emit(gap)
            elif kind == "n":
                self._emit(StreamNotice(self._next(), at, line["kind"], line["m"]))
        self._state("disconnected", broker.now(), "end of the recording")

    def _batch(self, event: EventType, values: list[Any], at: datetime) -> None:
        fields = self._fields.get(event)
        if not fields or len(values) % len(fields):
            return
        n, name = len(fields), event.value
        symbol_at = fields.index("eventSymbol")
        kept: list[Any] = []
        for i in range(0, len(values), n):
            row = values[i : i + n]
            key = (name, row[symbol_at])
            self._last[key] = row
            if key in self._subs:
                kept.extend(row)
        if kept:
            self._last_data_at = at
            self._emit(EventBatch(self._next(), event, fields, kept, at))


class ReplayBroker:
    """The implementation of :class:`s3t.broker.interface.Broker` over a recording."""

    def __init__(
        self,
        directory: Path,
        *,
        speed: float | None = 1.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if speed is not None and speed <= 0:
            raise ValueError("speed must be positive, or None for no waiting")
        self.directory = directory
        self.speed, self.sleep = speed, sleep
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
        with gzip.open(self.directory / STREAM, "rt", encoding="utf-8") as f:
            for text in f:
                yield json.loads(text)

    def _read(self, call: str, *args: Any) -> Any:
        line = self._rest.get((call, json.dumps(list(args))))
        if line is None or "result" not in line:
            raise NotFound(f"{call}: not in the recording {self.directory.name}", status=404)
        return line["result"]

    async def quote_token(self) -> QuoteToken:
        at = self.now()
        return QuoteToken("replay:", SecretStr("replay"), "replay", at, at + timedelta(days=1))

    async def equity(self, symbol: str) -> JsonObject:
        return self._read("equity", symbol)

    async def equity_option(self, symbol: str) -> JsonObject:
        return self._read("equity_option", symbol)

    async def option_chain(self, underlying: str) -> JsonObject:
        return self._read("option_chain", underlying)

    async def future(self, symbol: str) -> JsonObject:
        return self._read("future", symbol)

    async def future_option(self, symbol: str) -> JsonObject:
        return self._read("future_option", symbol)

    async def futures_option_chain(self, root: str) -> JsonObject:
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

    def now(self) -> datetime:
        return self.position

    async def aclose(self) -> None:
        if self._stream is not None:
            await self._stream.aclose()
