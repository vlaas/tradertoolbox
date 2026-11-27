"""What the day run (``tools/live_day.py``) and ``tools/timings.py reader`` measure, shared.

- :func:`option_set`: the options of a demand, with what an IV inversion needs of each.
- :class:`IvTaker`: the stand-in for MKT-04 (R1). At each flush of the market state it inverts
  the mids of the options whose quote changed, in one array call on the event loop, and times
  it. Only the cost is measured: the forward is the escrowed one on the spot, without
  dividends, so the values are not those R1 will compute (WP-1.1, WP-1.2).
- :class:`LagReader`: a reader of the feed beside the market state's, with the same queue rules.
  It notes how long each batch waited between its receipt and its take, and the times of the
  stream's states, for the forced drops.
- :func:`summary`: median, 95th percentile and maximum.
- :func:`machine`: what the timings ran on, so that they can be scaled to another machine
  (QA-08, decided 2026-11-27).

Every timing records the CPU time beside the wall time (decided 2026-11-27, owner): the wall time
says how long a step took on this machine under its load, the CPU time what it costs, and so how
much headroom is left. The event loop is one thread, so its headroom is the share of one core it
uses; the other cores are for the process pool of R1.

Nothing here keeps a price: the samples are times, sizes and counts.
"""

import asyncio
import math
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

from s3t.broker.instruments import Expiration, OptionChain
from s3t.broker.interface import EventBatch, EventType, Gap, StreamNotice, StreamState
from s3t.market.settlement import SessionTimes, settles_at
from s3t.market.state import MarketState
from s3t.pricing.clocks import tau_cal_array
from s3t.pricing.escrowed import escrowed_iv
from s3t.pricing.rates import RateCurve
from s3t.subs.feed import FillState, Lost
from s3t.subs.local import LocalFeed


def machine() -> dict[str, str | int | float | None]:
    """The processor, its cores and threads, the memory and the system."""
    cpu: str | None = platform.processor() or None
    cores: int | None = None
    memory: float | None = None
    try:
        if sys.platform == "darwin":

            def sysctl(name: str) -> str:
                return subprocess.run(
                    ["sysctl", "-n", name], capture_output=True, text=True, check=True
                ).stdout.strip()

            cpu = sysctl("machdep.cpu.brand_string")
            cores = int(sysctl("hw.physicalcpu"))
            memory = int(sysctl("hw.memsize")) / 2**30
        else:
            info = open("/proc/cpuinfo", encoding="utf-8").read()
            names = [
                x.split(":", 1)[1].strip() for x in info.splitlines() if x.startswith("model name")
            ]
            cpu = names[0] if names else cpu
            # One block per logical CPU; a core is a pair of a physical id and a core id.
            pairs = set()
            for block in info.split("\n\n"):
                keys = dict(
                    (k.strip(), v.strip())
                    for k, _, v in (x.partition(":") for x in block.splitlines())
                )
                if "core id" in keys:
                    pairs.add((keys.get("physical id"), keys["core id"]))
            cores = len(pairs) or None
            for line in open("/proc/meminfo", encoding="utf-8"):
                if line.startswith("MemTotal:"):
                    memory = int(line.split()[1]) / 2**20
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    return {
        "cpu": cpu,
        "cores": cores,
        "threads": os.cpu_count(),
        "memory_gb": None if memory is None else round(memory),
        "system": platform.platform(terse=True),
        "python": platform.python_version(),
    }


def summary(values: Sequence[float]) -> dict[str, float | int]:
    """The median, the 95th percentile (nearest rank) and the maximum, with the count."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    rank = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "n": len(ordered),
        "median": ordered[(len(ordered) - 1) // 2],
        "p95": ordered[rank],
        "max": ordered[-1],
    }


@dataclass(frozen=True)
class OptionSet:
    """The options of a demand, as parallel arrays."""

    streams: tuple[str, ...]
    underlying: tuple[str, ...]
    """The stream symbol of the underlying whose price is the spot."""
    strike: np.ndarray
    is_call: np.ndarray
    settles: np.ndarray
    """datetime64[us] in UTC; NaT where the instant is not known."""

    def __len__(self) -> int:
        return len(self.streams)


def expirations_from(chain: OptionChain, root: str, day: date, count: int) -> list[Expiration]:
    """The first ``count`` expirations of a root on or after a day."""
    return [e for e in chain.of_root(root) if e.expiration_date >= day][:count]


def option_set(
    picks: Sequence[tuple[Expiration, Sequence[int], str]], sessions: SessionTimes
) -> OptionSet:
    """The calls and puts at the chosen strike indexes of each expiration. ``picks`` holds the
    expiration, its strike indexes and the stream symbol of the spot."""
    streams: list[str] = []
    under: list[str] = []
    strikes: list[float] = []
    calls: list[bool] = []
    settles: list[np.datetime64] = []
    for expiration, indexes, spot in picks:
        at = settles_at(expiration, sessions)
        instant = np.datetime64("NaT", "us") if at is None else _utc64(at)
        for i in indexes:
            for symbol, call in (
                (expiration.call_streams[i], True),
                (expiration.put_streams[i], False),
            ):
                if symbol is None:
                    continue
                streams.append(symbol)
                under.append(spot)
                strikes.append(expiration.strikes[i])
                calls.append(call)
                settles.append(instant)
    return OptionSet(
        tuple(streams),
        tuple(under),
        np.array(strikes, dtype=np.float64),
        np.array(calls, dtype=bool),
        np.array(settles, dtype="datetime64[us]"),
    )


def _utc64(at: datetime) -> np.datetime64:
    return np.datetime64(round(at.timestamp() * 1_000_000), "us")


@dataclass
class IvSamples:
    """Per flush that had changed options: their count and the seconds of the inversion."""

    counts: list[int] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)
    cpu: list[float] = field(default_factory=list)
    """The CPU seconds of the event loop's thread for each inversion."""
    failed: int = 0
    flushes: int = 0
    codes: Counter[int] = field(default_factory=Counter)
    """The reason codes of ``s3t.pricing.black76`` of the inversions that failed."""

    def take(self) -> "IvSamples":
        taken = IvSamples(
            self.counts, self.seconds, self.cpu, self.failed, self.flushes, self.codes
        )
        self.counts, self.seconds, self.cpu, self.failed, self.flushes = [], [], [], 0, 0
        self.codes = Counter()
        return taken


class IvTaker:
    """At each flush, the IV of the options whose quote changed, in one array call."""

    def __init__(
        self,
        market: MarketState,
        options: OptionSet,
        *,
        now: Callable[[], datetime],
        curve: Callable[[], RateCurve],
        name: str = "measure-iv",
    ) -> None:
        self._market, self._options, self._now, self._curve = market, options, now, curve
        self._name = name
        self._index = {s: i for i, s in enumerate(options.streams)}
        self._spots = sorted(set(options.underlying))
        self.samples = IvSamples()

    async def run(self) -> None:
        changes = self._market.changes(self._name)
        try:
            async for flush in changes:
                self.samples.flushes += 1
                changed = flush.changed.get(EventType.QUOTE, frozenset())
                rows = [self._index[s] for s in changed if s in self._index]
                if rows:
                    self._invert(rows)
        finally:
            changes.close()

    def _invert(self, rows: list[int]) -> None:
        started, cpu = time.perf_counter(), time.thread_time()
        o, market = self._options, self._market
        symbols = [o.streams[i] for i in rows]
        bid = np.array(market.column(EventType.QUOTE, "bid", symbols))
        ask = np.array(market.column(EventType.QUOTE, "ask", symbols))
        spot_of = dict(zip(self._spots, self._spot_prices(), strict=True))
        spot = np.array([spot_of[o.underlying[i]] for i in rows])
        now = np.array([_utc64(self._now())], dtype="datetime64[us]")
        tau = tau_cal_array(now, o.settles[rows])
        rate = np.asarray(self._curve().rate(tau), dtype=np.float64)
        result = escrowed_iv(
            (bid + ask) / 2.0, spot, o.strike[rows], tau, rate, 0.0, o.is_call[rows]
        )
        self.samples.seconds.append(time.perf_counter() - started)
        self.samples.cpu.append(time.thread_time() - cpu)
        self.samples.counts.append(len(rows))
        failed = result.fail[result.fail != 0]
        self.samples.failed += len(failed)
        self.samples.codes.update(int(c) for c in failed)

    def _spot_prices(self) -> list[float]:
        """The last trade of each underlying; the quote's mid where there is no trade."""
        market = self._market
        trades = market.column(EventType.TRADE, "price", self._spots)
        bids = market.column(EventType.QUOTE, "bid", self._spots)
        asks = market.column(EventType.QUOTE, "ask", self._spots)
        return [
            t if math.isfinite(t) else (b + a) / 2.0
            for t, b, a in zip(trades, bids, asks, strict=True)
        ]


@dataclass
class ReadSamples:
    """What the reader took in a stretch of time."""

    lags: list[float] = field(default_factory=list)
    """Seconds from the receipt of a batch to its take, per batch."""
    events: Counter[str] = field(default_factory=Counter)
    batches: Counter[str] = field(default_factory=Counter)
    lost: int = 0

    def take(self) -> "ReadSamples":
        taken = ReadSamples(self.lags, self.events, self.batches, self.lost)
        self.lags, self.events, self.batches, self.lost = [], Counter(), Counter(), 0
        return taken


class LagReader:
    """A reader of every batch: the wait of each, and the times of the stream's states."""

    def __init__(
        self,
        feed: LocalFeed,
        *,
        now: Callable[[], datetime],
        name: str = "measure",
        watch: str | None = None,
    ) -> None:
        self._reader = feed.reader(name)
        self._now = now
        self.samples = ReadSamples()
        self.marks: list[tuple[float, str]] = []
        """(monotonic time, what) of the states, gaps, notices and fills, and of the first
        batch after each of them."""
        self._watch = watch
        self.watched = 0
        """Quote events of the watched stream symbol (the removal check)."""
        self._first_after = False

    async def run(self) -> None:
        try:
            async for item in self._reader:
                if isinstance(item, EventBatch):
                    lag = (self._now() - item.received_at).total_seconds()
                    self.samples.lags.append(lag)
                    self.samples.events[item.event.value] += item.count
                    self.samples.batches[item.event.value] += 1
                    if self._first_after:
                        self.marks.append((time.monotonic(), "data"))
                        self._first_after = False
                    if self._watch is not None and item.event is EventType.QUOTE:
                        self.watched += sum(1 for s in item.columns[0] if s == self._watch)
                elif isinstance(item, Lost):
                    self.samples.lost += item.batches
                elif isinstance(item, StreamState):
                    self.marks.append((time.monotonic(), f"phase:{item.phase}"))
                    self._first_after = True
                elif isinstance(item, FillState):
                    self.marks.append((time.monotonic(), f"fill:{item.phase}"))
                elif isinstance(item, Gap):
                    self.marks.append((time.monotonic(), "gap"))
                elif isinstance(item, StreamNotice):
                    self.marks.append((time.monotonic(), f"notice:{item.kind}"))
        finally:
            self._reader.close()

    async def first_mark(self, what: str, after: float, within: float) -> float | None:
        """The monotonic time of the first mark ``what`` after an instant; None when none
        comes within so many seconds."""
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            for at, seen in self.marks:
                if seen == what and at > after:
                    return at
            await asyncio.sleep(0.05)
        return None
