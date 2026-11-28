"""The latest values and what changed (MKT-02, MKT-03, MKT-07; GEN-10).

:class:`MarketState` is the one reader of the feed's quotes, trades, summaries, Greeks and
profiles. It asks for nothing: the views state their demand to the feed and read the values
here. Candles are not here. Their history arrives newest first and ends in a marker, so the
latest one received is not the latest bar; the candle module (R2) reads them.

What the rest of the application may rely on (decided 2026-11-24, WP-0.5):

- **The latest value is the last one received,** per symbol and event type, with the receipt
  time of its batch. An event is a whole record and replaces the one before it: a bid that
  has gone missing is missing, it does not keep its old value.
- **A missing number is NaN and a missing time is None** (MKT-07), as in the event records. A
  symbol that has no value yet reads as missing too, never as zero.
- **A pair changed when an event arrived for it, or when its value was dropped.** The values
  are not compared: of 2.9 million consecutive quotes of one day none equalled the one before
  it (note B, addendum "WP-0.5"), and after a reconnect an unchanged value that the broker
  confirms is news: it is live again.
- **A flush holds the changed pairs only.** It comes at the feed's aggregation period, and
  not at all when nothing changed. Each taker has its own sets of changed pairs. One that is
  late gets the union of what it missed in one flush, so nothing queues up and nothing is
  lost.
- **The value of a pair that is no longer granted is dropped** at the next flush, and the
  pair counts as changed. A released pair, or one the budget cut (SUB-03), would otherwise
  keep its last value and look live. Events still on their way are dropped with the next
  sweep.
- **After a reconnect the values stay.** :attr:`MarketState.live_since` says from which receipt
  time on a value is confirmed by the current session; None while there is no session. The
  same holds when batches were lost: which pairs they held is not known.
"""

import asyncio
import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import repeat
from typing import Any

from s3t.broker.interface import FIELDS, RECORDS, EventBatch, EventType, Record, StreamState
from s3t.broker.limits import Sleep
from s3t.subs.feed import Feed, FeedItem, Lost

EVENTS: tuple[EventType, ...] = tuple(e for e in EventType if e is not EventType.CANDLE)
SWEEP_S = 5.0
"""How often pairs that are not granted are looked for when the grant did not change."""
YIELD_EVERY = 64
"""Items after which the reader lets the event loop run. A reader with a full queue would
otherwise take all of it in one turn."""
LOG_EVERY = 1000

LOG = logging.getLogger(__name__)

type _Row = tuple[tuple[Any, ...], datetime]
"""The values in the order of the record's fields, and the receipt time."""


@dataclass(frozen=True, slots=True)
class Latest:
    """The latest value of a pair."""

    record: Record
    received_at: datetime
    """UTC. When its batch arrived. The first event after a subscription is the broker's
    current value, which can be old, so this is not the age of the value (MKT-06)."""


@dataclass(frozen=True, slots=True)
class Flush:
    """The pairs that changed since the flush before it, for one taker."""

    at: datetime
    """UTC, the time of the data."""
    changed: Mapping[EventType, frozenset[str]]
    """The stream symbols per event type. An event type without a change is not in it."""

    @property
    def symbols(self) -> frozenset[str]:
        """Every stream symbol with a change of any event type."""
        return frozenset().union(*self.changed.values())


@dataclass(frozen=True)
class MarketStatus:
    """The market state at this moment, for the health route (WP-0.7)."""

    values: Mapping[EventType, int]
    """Values held per event type."""
    period: float
    """Seconds between flushes."""
    live_since: datetime | None
    last_batch_at: datetime | None
    flushes: int
    """Flushes taken, over all takers."""
    lost: int
    """Batches that did not reach the market state."""
    failures: int
    """Unexpected errors. Each one is logged and the work goes on."""


class Changes:
    """One taker of flushes: ``async for flush in changes``."""

    def __init__(self, state: "MarketState", name: str) -> None:
        self._state, self.name = state, name
        self._dirty: dict[EventType, set[str]] = {event: set() for event in EVENTS}
        self._ready = asyncio.Event()
        self._closed = False

    def _mark(self, event: EventType, symbols: Iterable[str]) -> None:
        self._dirty[event].update(symbols)

    def _release(self) -> None:
        """The period is over: let the taker have what changed, if anything did."""
        if any(self._dirty.values()):
            self._ready.set()

    def _take(self) -> Flush:
        changed = {event: frozenset(symbols) for event, symbols in self._dirty.items() if symbols}
        for symbols in self._dirty.values():
            symbols.clear()
        self._ready.clear()
        return Flush(self._state.now(), changed)

    def __aiter__(self) -> "Changes":
        return self

    async def __anext__(self) -> Flush:
        while not self._closed:
            if self._ready.is_set():
                self._state.flushes += 1
                return self._take()
            await self._ready.wait()
        raise StopAsyncIteration

    def close(self) -> None:
        self._closed = True
        self._ready.set()
        self._state._forget(self)


class MarketState:
    """The latest values, read from a feed. Build it before the feed is started, so that it
    sees every item."""

    def __init__(
        self,
        feed: Feed,
        *,
        now: Callable[[], datetime],
        period: float,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._feed, self._sleep = feed, sleep
        self.now = now
        """The time of the data (the broker's ``data_time``)."""
        self._period = period
        self._reader = feed.reader("market", events=EVENTS)
        self._tables: dict[EventType, dict[str, _Row]] = {event: {} for event in EVENTS}
        self._takers: list[Changes] = []
        self._granted: Sequence[Any] = ()
        self._since_sweep = 0.0
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False
        self.live_since: datetime | None = None
        """UTC. A value received from here on is confirmed by the current session."""
        self.last_batch_at: datetime | None = None
        self.flushes = 0
        self.lost = 0
        self.failures = 0

    def __repr__(self) -> str:
        return f"MarketState({sum(map(len, self._tables.values()))} values)"

    # ------------------------------------------------------------------ reading

    def latest(self, event: EventType, symbol: str) -> Latest | None:
        """The latest value of a pair, by its stream symbol; None if there is none."""
        row = self._tables[event].get(symbol)
        return None if row is None else Latest(RECORDS[event](*row[0]), row[1])

    def column(self, event: EventType, field: str, symbols: Sequence[str]) -> list[float]:
        """One number per stream symbol, NaN where there is none: the input of an array
        operation (MKT-04) or of a wire column."""
        at = FIELDS[event].index(field)
        table, nan = self._tables[event], math.nan
        rows = (table.get(symbol) for symbol in symbols)
        return [nan if row is None else row[0][at] for row in rows]

    def instants(
        self, event: EventType, field: str, symbols: Sequence[str]
    ) -> list[datetime | None]:
        """One instant of the records per stream symbol, None where there is none or the record
        has none (WP-1.2: the bid and ask times of quotes, for the age of a quote)."""
        at = FIELDS[event].index(field)
        table = self._tables[event]
        rows = (table.get(symbol) for symbol in symbols)
        found = (None if row is None else row[0][at] for row in rows)
        return [x if isinstance(x, datetime) else None for x in found]

    def received(self, event: EventType, symbols: Sequence[str]) -> list[datetime | None]:
        """The receipt time per stream symbol, None where there is no value."""
        table = self._tables[event]
        rows = (table.get(symbol) for symbol in symbols)
        return [None if row is None else row[1] for row in rows]

    def is_live(self, received_at: datetime | None) -> bool:
        """Whether a value received then is confirmed by the current session."""
        since = self.live_since
        return since is not None and received_at is not None and received_at >= since

    def changes(self, name: str) -> Changes:
        """A taker of flushes from now on. It starts empty: read the values first wanted from
        the state, at once, and the flushes follow on from there."""
        taker = Changes(self, name)
        if self._closed:
            taker.close()
        else:
            self._takers.append(taker)
        return taker

    def status(self) -> MarketStatus:
        return MarketStatus(
            values={event: len(table) for event, table in self._tables.items()},
            period=self.period(),
            live_since=self.live_since,
            last_batch_at=self.last_batch_at,
            flushes=self.flushes,
            lost=self.lost,
            failures=self.failures,
        )

    def period(self) -> float:
        """Seconds between flushes: the period the server granted, else the configured one
        (a replay grants none)."""
        return self._feed.status().stream.aggregation_period or self._period

    # ------------------------------------------------------------------ running

    def start(self) -> None:
        if not self._tasks and not self._closed:
            self._tasks = [
                asyncio.create_task(self._read(), name="market-reader"),
                asyncio.create_task(self._flush(), name="market-flush"),
            ]

    async def aclose(self) -> None:
        self._end()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._reader.close()

    def _end(self) -> None:
        """Nothing comes any more: no value is live, and the takers end."""
        self._closed = True
        self.live_since = None
        for taker in list(self._takers):
            taker.close()

    def _forget(self, taker: Changes) -> None:
        if taker in self._takers:
            self._takers.remove(taker)

    def _failed(self, what: str) -> None:
        self.failures += 1
        if self.failures % LOG_EVERY == 1:
            LOG.exception("market state: %s failed (%d so far)", what, self.failures)

    # ------------------------------------------------------------------ the feed's items

    async def _read(self) -> None:
        """The reader never ends by an error: values that stop changing without a word would
        look like a quiet market."""
        taken = 0
        async for item in self._reader:
            try:
                self._take(item)
            except Exception:
                self._failed("an item of the feed")
                self._unconfirmed()
            taken += 1
            if taken % YIELD_EVERY == 0:
                await asyncio.sleep(0)
        self._end()

    def _take(self, item: FeedItem) -> None:
        if isinstance(item, EventBatch):
            self._ingest(item)
        elif isinstance(item, StreamState):
            self.live_since = item.at if item.phase == "connected" else None
        elif isinstance(item, Lost):
            self.lost += item.batches
            self._unconfirmed()

    def _unconfirmed(self) -> None:
        """Events did not get here, and of which pairs is not known: no value held now is
        confirmed."""
        if self.live_since is not None:
            self.live_since = self.now()

    def _ingest(self, batch: EventBatch) -> None:
        if not batch.count:
            return
        symbols = batch.columns[0]
        rows = zip(zip(*batch.columns, strict=True), repeat(batch.received_at))
        self._tables[batch.event].update(zip(symbols, rows, strict=True))
        self.last_batch_at = batch.received_at
        self._mark(batch.event, symbols)

    def _mark(self, event: EventType, symbols: Sequence[str]) -> None:
        for taker in self._takers:
            taker._mark(event, symbols)

    # ------------------------------------------------------------------ the flush

    async def _flush(self) -> None:
        while True:
            period = self.period()
            await self._sleep(period)
            try:
                self._sweep(period)
                for taker in list(self._takers):
                    taker._release()
            except Exception:
                self._failed("a flush")

    def _sweep(self, elapsed: float) -> None:
        """Drop the values of pairs that are not granted: at once after the grant changed,
        and every few seconds for events that were still on their way then."""
        self._since_sweep += elapsed
        granted = self._feed.granted()
        if granted is self._granted and self._since_sweep < SWEEP_S:
            return
        self._granted, self._since_sweep = granted, 0.0
        kept: dict[EventType, set[str]] = {event: set() for event in EVENTS}
        for sub in granted:
            if sub.event in kept:
                kept[sub.event].add(sub.symbol)
        for event, table in self._tables.items():
            gone = [symbol for symbol in table if symbol not in kept[event]]
            for symbol in gone:
                del table[symbol]
            if gone:
                self._mark(event, gone)
