"""The volatility surface of the underlyings that a view watches (PRC-05 to PRC-07 in use; PRF-05).

A view takes a :class:`Watch` on an underlying and releases it. While an underlying is watched the
service subscribes the quotes a fit needs, fits them in the process pool on a timer and on demand,
and keeps the latest result. Without a watch it does nothing: no consumer of the feed, no taker of
the market state, no task, and no task for the pool, which starts its processes with the first
fit. Decided 2026-11-27 (owner, WP-1.1; requirements, section 3, "The surface package", part 2).

**Which underlyings.** Stocks, ETFs and indexes. A futures product is not fitted yet; a watch on
one has a problem and no surface.

**Which expirations.** Every open one of a standard option root. An adjusted root (after a
split, a special dividend, a merger) delivers something else than the underlying, so put-call
parity on the underlying's price does not hold for it: it is left out, and the status counts what
was left out.

**The subscriptions.** One consumer of the feed, ``surface``, in the class of the views, for all
watched underlyings. Of each option only the quote. Per underlying:

- its own quote and trade, for the spot: the quote's mid of a stock or an ETF, else the last trade;
  the last trade of an index, whose quote is too wide to be a price;
- of every open expiration ``core_strikes`` strikes each side of the money, calls and puts: they
  give the parity forward and an at-the-money IV;
- of the expirations within ``max_dte`` days also the out-of-the-money strikes out to
  ``wing_deviations`` at-the-money standard deviations, at most ``wing_strikes`` a side.

The demand is stated in the order spots, cores, wings, each from the nearest expiration on, so a
budget that is short cuts the farthest wings first (SUB-03). A slice that lost its wings is
``sparse`` and takes the SSVI smile.

**The set is chosen in steps,** because each step needs what the one before brings: the spot
first, then the cores around it, then the wings, whose reach needs the at-the-money IV of a first
fit. The wings are thinned on a fixed lattice of the listed strikes (every n-th strike by its
index), and an expiration is chosen anew only when the money has moved by ``RECENTRE`` strikes, or
the money or the reach by the share ``KEEP`` of the reach. Otherwise every fit would change
subscriptions.

**When it fits.** Every ``refresh_s`` seconds, and not when no quote of the set changed since the
fit before. While the set fills (after a watch began or pairs were added) it looks every
``FILL_S`` seconds instead. The set has filled when the feed has sent all it was asked for and
``FILL_LOOKS`` looks in a row brought no new value. A fit on demand runs at once, whatever
changed; before the first values are there it waits for them. The fits of one underlying run one
after the other, each warm from the one before.

**Which quotes a fit uses.** The stored values, live or not: the result carries the time of the
data and the count of the values that the current session has not confirmed. An expiration is
left out from its settlement instant on.

**Failures.** A fit that fails is counted and logged, and the last surface stays with its data
time. The broker's documents are read once per New York day of the data; when they cannot be
read, the read is tried again and the ones of the day before stay in use.

**What runs on the event loop** is the choice of the set, the statement of the demand and the
gathering of the inputs; the longest of them is in the status (PRF-05). The fit itself, with the
forwards and the IV of the mids, runs in a worker (``s3t.analysis.surface_task``).
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from s3t.analysis import surface_task
from s3t.broker.instruments import Expiration, Security
from s3t.broker.interface import (
    BrokerError,
    BrokerUnavailable,
    EventType,
    ForbiddenCall,
    NotFound,
    RateLimited,
    Sub,
)
from s3t.broker.limits import Sleep
from s3t.config import SurfaceConfig
from s3t.market.registry import Instruments
from s3t.market.settlement import settles_at
from s3t.market.state import Changes, MarketState
from s3t.pricing import Dividend, model_forward, parity, pv_dividends, tau_cal
from s3t.pricing.rates import RateCurve
from s3t.pricing.surface import SliceInput, Surface, Warm
from s3t.refdata.calendar import ExchangeCalendar
from s3t.refdata.dividends import Dividends
from s3t.subs.feed import Demand, Feed, Priority
from s3t.system.pool import Pool

CONSUMER = "surface"
FILL_S = 2.0
"""Seconds between looks while the set of an underlying fills."""
FILL_LOOKS = 3
"""Looks in a row without a new value after which the set counts as filled."""
RETRY_S = 30.0
"""Seconds until the broker is asked again after it could not be."""
RECENTRE = 2
"""Strikes by which the money has to move before the core of an expiration moves."""
KEEP = 0.15
"""The wings of an expiration stay while the money has moved by less than this share of their
reach and the reach has changed by less than this share."""
LOG_EVERY = 100

_NY = ZoneInfo("America/New_York")
_DAY = timedelta(days=1)
_EXDIV_WINDOW = timedelta(days=2 * parity.EXDIV_DAYS + 4)  # holds that many sessions
_QUOTE, _TRADE = EventType.QUOTE, EventType.TRADE

LOG = logging.getLogger(__name__)


class SurfaceUnavailable(Exception):
    """There is no surface: the message says why."""


@dataclass(frozen=True)
class Expiry:
    """An expiration of a fit. ``key`` is the key of its slice in the surface."""

    key: str
    root: str
    expiration_date: date
    settles_at: datetime
    """UTC. The instant its options are priced to (``s3t.market.settlement``)."""


@dataclass(frozen=True, eq=False)
class SurfaceFit:
    """One fit of one underlying."""

    underlying: str
    number: int
    """Rises with every fit of this watch of the underlying, from 1."""
    at: datetime
    """UTC. The time of the data when the quotes were taken."""
    surface: Surface
    expirations: Mapping[str, Expiry]
    """By the key of the slice."""
    spot: float
    """The price of the underlying that the fit saw; NaN when it had none."""
    values: int
    """Option quotes among the inputs."""
    not_live: int
    """Of them, not confirmed by the current session of the stream."""
    fit_s: float
    """Seconds of the fit in its worker."""
    cpu_s: float
    """CPU seconds of the worker for it."""
    took_s: float
    """Seconds from handing the inputs to the pool until the result was back."""
    gather_s: float
    """Seconds on the event loop to take the quotes and make the inputs (PRF-05)."""
    select_s: float
    """Seconds on the event loop to choose the set again from the result."""


@dataclass(frozen=True)
class WatchedStatus:
    """One watched underlying, for the health route (SEC-08)."""

    underlying: str
    watchers: int
    problem: str | None
    """Why there is no surface, or why the last fit failed."""
    filling: bool
    """The set of pairs is still being chosen or its first values are still coming."""
    expirations: int
    """Expirations in the set."""
    left_out: int
    """Open expirations that the broker lists and the set does not hold: of a root that is not
    standard (an adjusted contract), or without a settlement instant."""
    pairs: int
    """Pairs asked of the feed. What the budget granted is in the use of the consumer."""
    fits: int
    skipped: int
    """Looks at which no quote had changed."""
    failures: int
    last_fit_at: datetime | None
    """UTC, the time of the data of the latest fit."""
    last_fit_s: float | None
    """Seconds of the latest fit in its worker."""
    values: int | None
    not_live: int | None
    slices: Mapping[str, int]
    """The slices of the latest fit by status: ok, sparse, fail, none."""
    loop_ms_max: float
    """The longest step of this underlying on the event loop so far, in milliseconds
    (PRF-05)."""


@dataclass(frozen=True)
class SurfaceStatus:
    refresh_s: float
    watched: tuple[WatchedStatus, ...]


# ---------------------------------------------------------------------- the choice of strikes


@dataclass(frozen=True)
class Choice:
    """The strikes of one expiration, as indexes into its listed strikes."""

    centre: int
    """The index of the first strike at or above the money when the core was placed."""
    core: tuple[int, int]
    """The core: the indexes from the first up to, not including, the second."""
    wing_money: float
    """The money the wings were chosen at; 0 without wings."""
    wing_reach: float
    """How far they reach, in log-moneyness; 0 without wings."""
    put_step: int
    call_step: int
    """Every so many-th listed strike is a wing strike."""
    puts: tuple[int, ...]
    """The put wing, from the money outwards."""
    calls: tuple[int, ...]

    @property
    def pairs(self) -> int:
        return 2 * (self.core[1] - self.core[0]) + len(self.puts) + len(self.calls)


def choose(
    strikes: np.ndarray,
    money: float,
    reach: float | None,
    *,
    core: int,
    wing: int,
    before: Choice | None = None,
) -> Choice:
    """The strikes of an expiration around ``money``. ``reach`` is how far the wings go, in
    log-moneyness; None when it is not known, and then the wings of ``before`` stay."""
    n = len(strikes)
    centre = int(np.searchsorted(strikes, money))
    if before is not None and abs(centre - before.centre) < RECENTRE:
        centre = before.centre
    lo, hi = max(0, centre - core), min(n, centre + core)
    wing_money, wing_reach, put_step, call_step = 0.0, 0.0, 1, 1
    kept = before is not None and before.wing_reach > 0
    if kept and reach is not None and reach > 0:
        assert before is not None
        moved = abs(math.log(money / before.wing_money))
        kept = moved <= KEEP * before.wing_reach and abs(reach / before.wing_reach - 1) <= KEEP
    if kept:
        assert before is not None
        wing_money, wing_reach = before.wing_money, before.wing_reach
        put_step, call_step = before.put_step, before.call_step
    elif reach is not None and reach > 0:
        wing_money, wing_reach = money, reach
    if not wing_reach > 0:
        return Choice(centre, (lo, hi), 0.0, 0.0, 1, 1, (), ())
    low = int(np.searchsorted(strikes, wing_money * math.exp(-wing_reach), "left"))
    high = int(np.searchsorted(strikes, wing_money * math.exp(wing_reach), "right"))
    if not kept:
        put_step = max(1, math.ceil(max(lo - low, 0) / wing))
        call_step = max(1, math.ceil(max(high - hi, 0) / wing))
    puts = tuple(i for i in range(lo - 1, low - 1, -1) if i % put_step == 0)[:wing]
    calls = tuple(i for i in range(hi, high) if i % call_step == 0)[:wing]
    return Choice(centre, (lo, hi), wing_money, wing_reach, put_step, call_step, puts, calls)


# ---------------------------------------------------------------------- the state of an underlying


@dataclass(frozen=True, eq=False)
class _Exp:
    """An open expiration and what does not change during a day."""

    key: str
    expiration: Expiration
    settles: datetime
    strikes: np.ndarray
    dividends: tuple[Dividend, ...]
    """The schedule's dividends that go ex up to the expiration date."""
    exdiv_near: bool
    """One of them goes ex within ``parity.EXDIV_DAYS`` sessions."""


@dataclass(frozen=True, eq=False)
class _Rows:
    """The chosen strikes of all expirations as flat parallel columns, for the gathering."""

    spans: tuple[tuple[_Exp, int, int], ...]
    """Each expiration with the range of its rows."""
    strike: np.ndarray
    calls: tuple[str, ...]
    """Stream symbols; empty where that side is not asked for or not listed."""
    puts: tuple[str, ...]
    core: tuple[Sub, ...]
    wings: tuple[Sub, ...]
    symbols: frozenset[str]


class _Underlying:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.watchers = 0
        self.task: asyncio.Task[None] | None = None
        self.closed = False
        self.security: Security | None = None
        self.day: date | None = None
        """The New York day the documents were read for."""
        self.exps: tuple[_Exp, ...] = ()
        self.left_out = 0
        self.choices: dict[str, Choice] = {}
        self.rows: _Rows | None = None
        self.reread = False
        """The documents were read again: the rows are of the ones before."""
        self.spot_subs: tuple[Sub, ...] = ()
        self.symbols: frozenset[str] = frozenset()
        self.changed = False
        """A quote of the set changed since the inputs of the last fit were taken."""
        self.wake = asyncio.Event()
        self.news = asyncio.Event()
        """Set, and replaced, whenever a fit ended or the state changed for good."""
        self.wanted = 0
        """The number of the fit that a fit on demand waits for."""
        self.runs = 0
        """Fits begun."""
        self.ended = 0
        self.failed_run = 0
        """The number of the last fit that failed."""
        self.latest: SurfaceFit | None = None
        self.warm: Warm | None = None
        self.filling = True
        self.present = 0
        """Values of the set in the market state at the last look."""
        self.quiet = 0
        """Looks in a row that brought no new value."""
        self.fits = self.skipped = self.failures = 0
        self.problem: str | None = None
        self.final = False
        """The problem will not go away by trying again."""
        self.loop_s = 0.0

    def tell(self) -> None:
        news, self.news = self.news, asyncio.Event()
        news.set()

    def step(self, started: float) -> None:
        """Note how long a step on the event loop took."""
        self.loop_s = max(self.loop_s, time.perf_counter() - started)


class Watch:
    """A view's hold on the surface of one underlying. Release it when the view closes."""

    def __init__(self, service: "SurfaceService", state: _Underlying) -> None:
        self._service, self._state = service, state
        self._released = False

    @property
    def underlying(self) -> str:
        return self._state.symbol

    @property
    def latest(self) -> SurfaceFit | None:
        """The latest fit; None before the first one."""
        return self._state.latest

    @property
    def problem(self) -> str | None:
        """Why there is no surface, or why the last fit failed; None when all is well."""
        return self._state.problem

    async def next(self, after: int | None = None) -> SurfaceFit:
        """The first fit with a number above ``after``; without it, the first one from now on.
        Raises :class:`SurfaceUnavailable` when there will be none."""
        u = self._state
        if after is None:
            after = u.latest.number if u.latest is not None else 0
        while u.latest is None or u.latest.number <= after:
            self._alive()
            await u.news.wait()
        return u.latest

    async def fit(self) -> SurfaceFit:
        """A fit on the quotes as they are now, at once and whatever has changed. Raises
        :class:`SurfaceUnavailable` when it fails or cannot be made."""
        u = self._state
        self._alive()
        need = u.wanted = max(u.wanted, u.runs + 1)
        u.wake.set()
        while u.ended < need:
            await u.news.wait()
            self._alive()
        if u.latest is None or u.failed_run >= need:
            raise SurfaceUnavailable(u.problem or "the fit failed")
        return u.latest

    def _alive(self) -> None:
        u = self._state
        if self._released or u.closed:
            raise SurfaceUnavailable("the watch was released")
        if u.final:
            raise SurfaceUnavailable(u.problem or "there is no surface")

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._service._release(self._state)


class SurfaceService:
    def __init__(
        self,
        config: SurfaceConfig,
        *,
        pool: Pool,
        feed: Feed,
        market: MarketState,
        instruments: Instruments,
        dividends: Dividends,
        calendar: ExchangeCalendar,
        curve: Callable[[datetime], RateCurve],
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._config, self._pool, self._feed, self._market = config, pool, feed, market
        self._instruments, self._dividends, self._calendar = instruments, dividends, calendar
        self._curve, self._sleep = curve, sleep
        self._now = market.now
        self._watched: dict[str, _Underlying] = {}
        self._demand: Demand | None = None
        self._changes: Changes | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def __repr__(self) -> str:
        return f"SurfaceService({len(self._watched)} watched)"

    # ------------------------------------------------------------------ for the views

    def watch(self, underlying: str) -> Watch:
        """Take a watch on an underlying. Returns at once; the first fit comes when its quotes
        are there."""
        if self._closed:
            raise RuntimeError("the surface service is closed")
        u = self._watched.get(underlying)
        if u is None:
            u = self._watched[underlying] = _Underlying(underlying)
            if self._demand is None:
                self._demand = self._feed.consumer(CONSUMER, Priority.VIEWS)
                self._changes = self._market.changes(CONSUMER)
                self._start(self._take(self._changes), "surface-changes")
            u.task = self._start(self._run(u), f"surface:{underlying}")
        u.watchers += 1
        return Watch(self, u)

    def status(self) -> SurfaceStatus:
        return SurfaceStatus(
            refresh_s=self._config.refresh_s,
            watched=tuple(self._status_of(u) for u in self._watched.values()),
        )

    async def aclose(self) -> None:
        self._closed = True
        for u in list(self._watched.values()):
            u.watchers = 0
            self._drop(u)
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------------ watches

    def _start(self, work: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(work, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _release(self, u: _Underlying) -> None:
        u.watchers -= 1
        if u.watchers <= 0:
            self._drop(u)

    def _drop(self, u: _Underlying) -> None:
        """Nobody watches it any more: everything of it goes, at once."""
        if self._watched.get(u.symbol) is u:
            del self._watched[u.symbol]
        u.closed = True
        if u.task is not None:
            u.task.cancel()
        u.tell()
        if self._watched:
            self._state_demand()
        else:
            if self._demand is not None:
                self._demand.close()
                self._demand = None
            if self._changes is not None:
                self._changes.close()  # its task ends with it
                self._changes = None

    def _status_of(self, u: _Underlying) -> WatchedStatus:
        fit, rows = u.latest, u.rows
        slices: dict[str, int] = {}
        for s in fit.surface.slices if fit is not None else ():
            slices[s.status] = slices.get(s.status, 0) + 1
        pairs = len(u.spot_subs) + (0 if rows is None else len(rows.core) + len(rows.wings))
        return WatchedStatus(
            underlying=u.symbol,
            watchers=u.watchers,
            problem=u.problem,
            filling=u.filling,
            expirations=0 if rows is None else len(rows.spans),
            left_out=u.left_out,
            pairs=pairs,
            fits=u.fits,
            skipped=u.skipped,
            failures=u.failures,
            last_fit_at=None if fit is None else fit.at,
            last_fit_s=None if fit is None else round(fit.fit_s, 4),
            values=None if fit is None else fit.values,
            not_live=None if fit is None else fit.not_live,
            slices=slices,
            loop_ms_max=round(u.loop_s * 1000, 2),
        )

    # ------------------------------------------------------------------ what changed

    async def _take(self, changes: Changes) -> None:
        async for flush in changes:
            changed = flush.changed.get(_QUOTE, frozenset()) | flush.changed.get(
                _TRADE, frozenset()
            )
            for u in self._watched.values():
                if not u.changed and not changed.isdisjoint(u.symbols):
                    u.changed = True

    # ------------------------------------------------------------------ one underlying

    async def _run(self, u: _Underlying) -> None:
        while not await self._read(u):
            if u.final:
                u.tell()
                return
            await self._sleep(RETRY_S)
        while True:
            await self._wait(u, FILL_S if u.filling else self._config.refresh_s)
            try:
                await self._look(u)
            except Exception as e:
                # A fit that was asked for has failed with it, or the wait would not wait.
                u.runs = max(u.runs, u.wanted)
                u.ended = u.failed_run = u.runs
                self._failed(u, f"the surface service failed ({type(e).__name__})", unexpected=True)
            if u.final:
                return

    async def _wait(self, u: _Underlying, seconds: float) -> None:
        """Until the time is over, or a fit on demand is asked for and can be made."""
        u.wake.clear()
        if u.wanted > u.runs and self._can_fit(u):
            return
        timer = asyncio.ensure_future(self._sleep(seconds))
        woken = asyncio.ensure_future(u.wake.wait())
        try:
            await asyncio.wait({timer, woken}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (timer, woken):
                task.cancel()
            await asyncio.gather(timer, woken, return_exceptions=True)

    @staticmethod
    def _can_fit(u: _Underlying) -> bool:
        """Whether a fit has something to work on: the set is chosen and a value of it is
        there, or none came while it filled."""
        return u.rows is not None and (u.present > 0 or not u.filling)

    def _failed(self, u: _Underlying, problem: str, *, unexpected: bool = False) -> None:
        u.failures += 1
        u.problem = problem
        if u.failures % LOG_EVERY == 1:
            log = LOG.exception if unexpected else LOG.warning
            log("surface: %s: %s (%d failures so far)", u.symbol, problem, u.failures)
        u.tell()

    async def _read(self, u: _Underlying) -> bool:
        """Read the documents of an underlying for the day of the data. False when that failed;
        ``u.final`` says whether another try can help."""
        try:
            security = await self._instruments.security(u.symbol)
            chain = await self._instruments.chain(u.symbol)
            payer = security.kind != "index"
            source = await self._dividends.of(u.symbol) if payer else None
        except (NotFound, ForbiddenCall):
            u.problem, u.final = "the broker knows no stock, ETF or index of this symbol", True
            return False
        except (BrokerUnavailable, RateLimited):
            u.problem = "the broker could not be asked; it is tried again"
            return False
        except BrokerError as e:
            LOG.warning("surface: the documents of %s could not be read: %s", u.symbol, e)
            u.problem, u.final = "the broker's answer is an error; the log has it", True
            return False
        started = time.perf_counter()
        at = self._now()
        today = at.astimezone(_NY).date()
        calendar = self._calendar
        exps: list[_Exp] = []
        left_out = 0
        for e in chain.expirations:
            settles = settles_at(e, calendar) if e.family == "equity" else None
            if settles is not None and settles <= at:
                continue  # it is over
            if settles is None or not e.standard or not e.strikes:
                # An adjusted contract delivers something else than the underlying, so parity
                # on the underlying's price does not hold for it.
                left_out += 1
                continue
            schedule = () if source is None else tuple(source.schedule(today, e.expiration_date))
            near = any(
                1 <= len(calendar.trading_days(today + _DAY, d.ex_date)) <= parity.EXDIV_DAYS
                for d in schedule
                if d.ex_date - today <= _EXDIV_WINDOW
            )
            key = f"{e.root}:{e.expiration_date.isoformat()}"
            exps.append(
                _Exp(key, e, settles, np.array(e.strikes, dtype=np.float64), schedule, near)
            )
        exps.sort(key=lambda x: x.settles)
        u.security, u.day, u.exps, u.left_out = security, today, tuple(exps), left_out
        u.choices = {x.key: u.choices[x.key] for x in exps if x.key in u.choices}
        u.reread = u.rows is not None
        stream = security.stream_symbol
        u.spot_subs = (Sub(stream, _QUOTE), Sub(stream, _TRADE))
        u.symbols = frozenset({stream}) | (u.rows.symbols if u.rows is not None else frozenset())
        u.step(started)
        if not exps:
            u.problem, u.final = "no open expiration is listed", True
            return False
        u.problem = None
        started = time.perf_counter()
        self._state_demand()
        u.step(started)
        return True

    async def _look(self, u: _Underlying) -> None:
        """One turn of the timer: choose the set while it is not there, and fit when something
        changed or a fit is asked for."""
        if self._now().astimezone(_NY).date() != u.day:
            # The documents of the day before stay when it fails for now.
            if await self._read(u):
                surface = None if u.latest is None else u.latest.surface
                self._select(u, self._now(), surface)
            elif u.final:
                u.tell()
                return
        at = self._now()
        if u.rows is None:
            self._select(u, at, None)
            self._filled(u, added=u.rows is not None)
            return
        if u.filling:
            self._filled(u, added=False)
        if not self._can_fit(u):
            return
        if u.wanted <= u.runs and not u.changed:
            u.skipped += 1
            return
        await self._fit(u, at)

    def _filled(self, u: _Underlying, *, added: bool) -> None:
        """Whether the set still fills: pairs were added, the feed has not sent all it was asked
        for, or a value came that was not there. The feed sends in the order of the demand and
        at its own pace, so the pairs of one underlying can wait for those of another."""
        rows = u.rows
        present = 0
        if rows is not None:
            received = self._market.received(_QUOTE, rows.calls + rows.puts)
            present = sum(r is not None for r in received)
        feed = self._feed.status()
        sending = feed.pending > 0 or feed.stream.phase != "connected"
        u.quiet = 0 if added or sending or present > u.present else u.quiet + 1
        u.present = present
        u.filling = u.quiet < FILL_LOOKS

    async def _fit(self, u: _Underlying, at: datetime) -> None:
        started = time.perf_counter()
        u.changed = False
        u.runs += 1
        number = u.runs
        inputs, expiries, spot, values, not_live = self._gather(u, at)
        gather_s = time.perf_counter() - started
        u.step(started)
        if not inputs:
            u.ended, u.failed_run = number, number
            self._failed(u, "no open expiration is left")
            return
        handed = time.perf_counter()
        try:
            fitted = await self._pool.run(surface_task.fit, inputs, u.warm, None)
        except Exception as e:
            u.ended, u.failed_run = number, number
            self._failed(u, f"the fit failed ({type(e).__name__})", unexpected=True)
            return
        took = time.perf_counter() - handed
        started = time.perf_counter()
        added = self._select(u, self._now(), fitted.surface)
        u.latest = SurfaceFit(
            underlying=u.symbol,
            number=number,
            at=at,
            surface=fitted.surface,
            expirations=expiries,
            spot=spot,
            values=values,
            not_live=not_live,
            fit_s=fitted.wall_s,
            cpu_s=fitted.cpu_s,
            took_s=took,
            gather_s=gather_s,
            select_s=time.perf_counter() - started,
        )
        u.warm = fitted.surface.warm()
        u.fits += 1
        u.ended = number
        u.problem = None
        if added:
            self._filled(u, added=True)
        u.step(started)
        u.tell()

    # ------------------------------------------------------------------ the inputs of a fit

    def _spot(self, u: _Underlying) -> float:
        """The price of the underlying: the quote's mid of a stock or an ETF, else the last
        trade; the last trade of an index. NaN when there is none."""
        assert u.security is not None
        stream, market = [u.security.stream_symbol], self._market
        if u.security.kind != "index":
            bid = market.column(_QUOTE, "bid", stream)[0]
            ask = market.column(_QUOTE, "ask", stream)[0]
            if bid > 0 and ask >= bid:
                return (bid + ask) / 2
        last = market.column(_TRADE, "price", stream)[0]
        return last if last > 0 else math.nan

    def _carry(
        self, x: _Exp, spot: float, at: datetime, today: date, curve: RateCurve
    ) -> tuple[float, float, float, float]:
        """Time to the settlement, the discount factor, the PV of the dividends before it and
        the model forward (NaN without a spot)."""
        tau = tau_cal(at, x.settles)
        rate = float(curve.rate(tau))
        pv = pv_dividends(x.dividends, today, rate) if x.dividends else 0.0
        model = float(model_forward(spot, rate, tau, pv)) if math.isfinite(spot) else math.nan
        return tau, float(curve.discount(tau)), pv, model

    def _gather(
        self, u: _Underlying, at: datetime
    ) -> tuple[list[SliceInput], dict[str, Expiry], float, int, int]:
        """The quotes of the set as the inputs of a fit, with the count of the option quotes
        among them and of those that are not live."""
        rows, market = u.rows, self._market
        assert rows is not None
        nan = math.nan
        column = market.column
        call_bid = np.array(column(_QUOTE, "bid", rows.calls), dtype=np.float64)
        call_ask = np.array(column(_QUOTE, "ask", rows.calls), dtype=np.float64)
        put_bid = np.array(column(_QUOTE, "bid", rows.puts), dtype=np.float64)
        put_ask = np.array(column(_QUOTE, "ask", rows.puts), dtype=np.float64)
        received = market.received(_QUOTE, rows.calls + rows.puts)
        since = market.live_since
        values = not_live = 0
        for r in received:
            if r is not None:
                values += 1
                not_live += since is None or r < since
        spot = self._spot(u)
        curve = self._curve(at)
        today = at.astimezone(_NY).date()
        inputs: list[SliceInput] = []
        expiries: dict[str, Expiry] = {}
        for x, a, b in rows.spans:
            if x.settles <= at:
                continue
            tau, df, pv, model = self._carry(x, spot, at, today, curve)
            inputs.append(
                SliceInput(
                    key=x.key,
                    tau=tau,
                    df=df,
                    strike=rows.strike[a:b],
                    call_bid=call_bid[a:b],
                    call_ask=call_ask[a:b],
                    put_bid=put_bid[a:b],
                    put_ask=put_ask[a:b],
                    spot=spot if math.isfinite(spot) else nan,
                    model=model,
                    pv_div=pv,
                    exdiv_near=x.exdiv_near,
                )
            )
            e = x.expiration
            expiries[x.key] = Expiry(x.key, e.root, e.expiration_date, x.settles)
        return inputs, expiries, spot, values, not_live

    # ------------------------------------------------------------------ the set of pairs

    def _select(self, u: _Underlying, at: datetime, surface: Surface | None) -> bool:
        """Choose the strikes of every open expiration from what is known now, and state the
        demand when the set changed. Whether pairs were added."""
        started = time.perf_counter()
        config = self._config
        spot = self._spot(u)
        curve = self._curve(at)
        today = at.astimezone(_NY).date()
        slices = {} if surface is None else {s.key: s for s in surface.slices}
        known = sorted(
            (s.tau, s.atm_iv) for s in slices.values() if s.atm_iv > 0 and math.isfinite(s.atm_iv)
        )
        choices: dict[str, Choice] = {}
        for x in u.exps:
            if x.settles <= at:
                continue
            before = u.choices.get(x.key)
            tau, _, _, model = self._carry(x, spot, at, today, curve)
            fitted = slices.get(x.key)
            money = fitted.forward if fitted is not None else math.nan
            if not (math.isfinite(money) and money > 0):
                money = model
            if not (math.isfinite(money) and money > 0):
                if before is not None:
                    choices[x.key] = before
                continue
            reach = None
            if (x.expiration.expiration_date - today).days <= config.max_dte and known:
                vol = fitted.atm_iv if fitted is not None else math.nan
                if not (math.isfinite(vol) and vol > 0):
                    vol = min(known, key=lambda k: abs(k[0] - tau))[1]
                reach = config.wing_deviations * vol * math.sqrt(tau)
            choices[x.key] = choose(
                x.strikes,
                money,
                reach,
                core=config.core_strikes,
                wing=config.wing_strikes,
                before=before,
            )
        if choices == u.choices and u.rows is not None and not u.reread:
            u.step(started)
            return False
        u.choices, u.reread = choices, False
        rows = _rows([x for x in u.exps if x.key in choices], choices) if choices else None
        old = u.rows.symbols if u.rows is not None else frozenset()
        new = rows.symbols if rows is not None else frozenset()
        assert u.security is not None
        u.rows, u.symbols = rows, new | {u.security.stream_symbol}
        if new != old:
            self._state_demand()
        u.step(started)
        return bool(new - old)

    def _state_demand(self) -> None:
        """The demand of all watched underlyings: spots, then cores, then wings."""
        if self._demand is None:
            return
        watched = list(self._watched.values())
        subs = [sub for u in watched for sub in u.spot_subs]
        subs += [sub for u in watched if u.rows is not None for sub in u.rows.core]
        subs += [sub for u in watched if u.rows is not None for sub in u.rows.wings]
        self._demand.set(subs)


def _rows(exps: Sequence[_Exp], choices: Mapping[str, Choice]) -> _Rows:
    spans: list[tuple[_Exp, int, int]] = []
    strike: list[float] = []
    calls: list[str] = []
    puts: list[str] = []
    core: list[Sub] = []
    wings: list[Sub] = []
    for x in exps:
        choice, e = choices[x.key], x.expiration
        lo, hi = choice.core
        first = len(strike)
        for i in sorted(choice.puts):
            strike.append(e.strikes[i])
            calls.append("")
            puts.append(e.put_streams[i] or "")
        for i in range(lo, hi):
            strike.append(e.strikes[i])
            calls.append(e.call_streams[i] or "")
            puts.append(e.put_streams[i] or "")
        for i in choice.calls:
            strike.append(e.strikes[i])
            calls.append(e.call_streams[i] or "")
            puts.append("")
        spans.append((x, first, len(strike)))
        # From the money outwards, so that a cut takes the far strikes.
        middle = sorted(range(lo, hi), key=lambda i: abs(i - choice.centre + 0.5))
        core += [Sub(s, _QUOTE) for s in e.streams(tuple(middle))]
        outwards = [
            stream
            for pair in zip_longest_pairs(
                [e.put_streams[i] for i in choice.puts], [e.call_streams[i] for i in choice.calls]
            )
            for stream in pair
            if stream
        ]
        wings += [Sub(s, _QUOTE) for s in outwards]
    symbols = frozenset(s for s in calls if s) | frozenset(s for s in puts if s)
    return _Rows(
        tuple(spans),
        np.array(strike, dtype=np.float64),
        tuple(calls),
        tuple(puts),
        tuple(core),
        tuple(wings),
        symbols,
    )


def zip_longest_pairs(
    a: Sequence[str | None], b: Sequence[str | None]
) -> list[tuple[str | None, str | None]]:
    """The two sequences side by side, the longer one going on alone."""
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else None, b[i] if i < len(b) else None) for i in range(n)]
