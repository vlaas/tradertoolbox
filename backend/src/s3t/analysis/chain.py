"""The option chain's views: quotes, own IV and Greeks by expiration and strike window (WP-1.2).

Requirements: MKT-04 (own IV and Greeks per flush, one array call), MKT-05 (an index), MKT-06
(stale quotes), PRC-13 (the broker's Greeks as a marked fallback), SUB-04 (the grace period),
SUB-05 (windows, never a whole chain), REF-03 (earnings and ex-dividend dates), PRF-03. Proposed
and decided 2026-11-28 (owner; requirements, section 3, "The chain package").

**A view** is opened on an underlying with up to ``most_windows`` windows. A window is an
expiration and a range of strikes, or the ``around_strikes`` strikes each side of the money when
it names no range; it holds ``most_strikes`` at most. A view gives a snapshot and then the parts
that changed: the head (the underlying), the expirations, the rows, the notes.

**One service for all views.** It holds the union of the views' contracts, one taker of the market
state and one call of the pricing core per flush (s3t.pricing.chain.contract_values), over every
contract that is due:

- its bid or its ask changed (a change of sizes alone is not);
- the forward of its expiration moved by ``FORWARD_MOVE`` or more since it was computed;
- it was computed ``RECOMPUTE_S`` seconds ago or more;
- it was never computed.

From the settlement instant of its expiration on, nothing is computed: the quotes are shown
without an IV (product definition 11.1). The flush runs on the event loop; its time is in the
status (PRF-03, PRF-05).

**The forward of an expiration** is the latest surface fit's, scaled by the move of the spot since
that fit. Without one, a stock or an ETF takes the model forward from its spot, its dividend
schedule and the rate curve; an index has none until the first fit, which comes a few seconds
after the watch (MKT-05: its price is its last trade, its forward from parity). The service takes
a watch on the surface of every underlying it holds (``s3t.analysis.surface``). An adjusted root
is not in the surface and has no forward of its own.

**Greeks** are spot Greeks in trader units with a multiplier and a quantity of 1, that is per
share: delta as a fraction, gamma per dollar, theta in dollars a calendar day, vega in dollars a
volatility point (requirements 4.6). Where the own inversion fails, the broker's Greeks are shown
and marked as the broker's (PRC-13); they are taken to be in the same units, which WP-1.9 checks.

**The subscriptions.** Two consumers of the feed: ``chain`` in the class of the views for the open
views, ``chain-held`` in the background class for the views closed in the last ``grace_s``
seconds (SUB-04). Per contract a quote, then the broker's Greeks, then the summary (open
interest) and the trade (volume); the quotes of all windows first, from the money outwards, so a
budget that is short cuts the extras of the far strikes first. The underlying's quote and trade
come before everything. The consumer of the views is registered before the surface's, so the
visible rows come first in their class.

**Stale** (MKT-06): a quote older than ``stale_after_s``. Its age runs from its own bid or ask
time where the feed gives one and from its receipt otherwise. The quotes of SPXW options carry no
time (note B), and the first event after a subscription has a fresh receipt time though its value
can be hours old: such a value is not caught.

Without a view the service does nothing: no consumer, no taker, no watch, no task.
"""

import asyncio
import logging
import math
import time
from collections import Counter, deque
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal, NamedTuple
from zoneinfo import ZoneInfo

import numpy as np

from s3t.analysis.surface import SurfaceService, Watch, spot_of
from s3t.analysis.units import position_units
from s3t.broker.instruments import Expiration, OptionChain, Security
from s3t.broker.interface import (
    BrokerError,
    BrokerUnavailable,
    EventType,
    ForbiddenCall,
    MarketMetrics,
    NotFound,
    RateLimited,
    Sub,
    TimeOfDay,
)
from s3t.broker.limits import Sleep
from s3t.config import ChainConfig
from s3t.market.registry import Instruments
from s3t.market.settlement import settles_at
from s3t.market.state import Changes, MarketState
from s3t.pricing import Dividend, model_forward, pv_dividends, pv_dividends_dr, tau_cal
from s3t.pricing.black76 import FAIL_NAMES, FAIL_OK
from s3t.pricing.chain import contract_values
from s3t.pricing.moves import METHOD, expected_move, mids
from s3t.pricing.rates import RateCurve
from s3t.pricing.surface import Slice
from s3t.refdata.calendar import ExchangeCalendar
from s3t.refdata.dividends import Dividends
from s3t.refdata.metrics import Metrics
from s3t.subs.feed import Demand, Feed, Priority, Reader, Reduction

CONSUMER, HELD = "chain", "chain-held"
RECOMPUTE_S = 30.0
"""A contract is computed again when its values are this old, so that time passes in them."""
FORWARD_MOVE = 1e-4
"""... or when the forward of its expiration has moved by this share: 1 basis point."""
LOOK_S = 1.0
"""A view looks this often for stale marks that flipped and for cuts of the budget."""
HEAD_S = 5.0
"""The expirations' figures are made again this often: forward, expected move, IVs."""
SPOT_WAIT_S = 3.0
"""How long a new underlying waits for its price before the windows around the money are set."""
RETRY_S = 30.0
LOG_EVERY = 100
SOLVER_LOGGER = "s3t.pricing.bsm"

OWN, BROKER, NONE = "own", "broker", "none"
type Source = Literal["own", "broker", "none"]
type NoteKind = Literal["unknown", "unreachable", "cut", "left_out", "window"]

_NY = ZoneInfo("America/New_York")
_QUOTE, _TRADE, _SUMMARY, _GREEKS = (
    EventType.QUOTE,
    EventType.TRADE,
    EventType.SUMMARY,
    EventType.GREEKS,
)
_GREEK_KEYS = ("delta", "gamma", "theta", "vega")
_NO_GREEKS = (math.nan, math.nan, math.nan, math.nan)

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------- the solver's warnings


class RepeatLimit(logging.Filter):
    """Lets a warning through the first time and then every ``every``-th time, with its count.
    The ported IV solver logs on every call that meets a bad row, and the chain calls it on
    every flush (requirements 4.6, "PRC-01 and logging"). Below a warning nothing is limited."""

    def __init__(self, every: int = LOG_EVERY) -> None:
        super().__init__()
        self.every = every
        self.seen: dict[tuple[str, object], int] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING:
            return True
        key = (record.name, record.msg)  # the template, so the table stays small
        n = self.seen[key] = self.seen.get(key, 0) + 1
        if n == 1:
            return True
        if n % self.every:
            return False
        record.msg, record.args = f"{record.getMessage()} ({n} times so far)", None
        return True


def limit_repeats(name: str = SOLVER_LOGGER) -> RepeatLimit:
    """Put the filter on a logger once; the one already there is returned."""
    logger = logging.getLogger(name)
    for f in logger.filters:
        if isinstance(f, RepeatLimit):
            return f
    limit = RepeatLimit()
    logger.addFilter(limit)
    return limit


# ---------------------------------------------------------------------- what a view gives


class ChainUnavailable(Exception):
    """There is no such chain or expiration, or the broker cannot be asked: the message says
    which."""

    def __init__(self, message: str, *, unknown: bool) -> None:
        super().__init__(message)
        self.unknown = unknown


class WindowRefused(ValueError):
    """A view asked for more than a view may hold (SUB-05), or for a range that is not one."""


@dataclass(frozen=True)
class Window:
    expiration: str
    """The key of the expiration: its root and date, ``SPXW:2026-12-18``."""
    low: float | None = None
    high: float | None = None
    """The strikes from ``low`` to ``high``, both included. Neither: around the money."""


@dataclass(frozen=True)
class Head:
    """The underlying of a view."""

    underlying: str
    kind: str | None
    """stock, etf or index; None while the broker has not been asked."""
    spot: float
    spot_source: str
    """quote (the mid), trade (the last) or none."""
    at: datetime
    """UTC, the time of the data."""
    stale_after_s: float
    earnings: date | None
    """The next report date the broker states; None when it states none ahead."""
    earnings_time: TimeOfDay | None
    earnings_estimated: bool | None


@dataclass(frozen=True)
class ExpirationInfo:
    key: str
    root: str
    expiration_date: date
    settlement: str
    """am or pm."""
    settles_at: datetime | None
    """UTC; None when the calendar cannot give it."""
    kind: str
    multiplier: float
    standard: bool
    """False for an adjusted root: no forward, no own IV, no surface."""
    strikes: int
    forward: float
    forward_source: str
    """parity, model, or none (s3t.pricing.parity); future is not served yet."""
    atm_iv: float
    """The surface's at-the-money IV of the expiration; NaN without a fit."""
    expected_move: float
    """In price units; NaN without the quotes at the money (s3t.pricing.moves)."""
    expected_move_method: str
    broker_iv: float
    """The broker's IV of the expiration (its IVx), never mixed with the own one (VOL-02)."""
    ex_dividends: tuple[Dividend, ...]
    """The schedule's ex-dates after today up to the expiration date (REF-02, REF-03)."""
    earnings_before: bool
    """The next earnings report falls before the settlement instant (REF-03)."""
    note: str | None
    """Why its contracts have no own IV, if that is so for all of them."""


class Side(NamedTuple):
    """One contract of a row. A tuple, because a view makes a thousand of them per flush."""

    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    quote_at: datetime | None
    """UTC. The time the age of the quote runs from (MKT-06); None without a quote."""
    stale: bool
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    source: Source
    """Whose IV and Greeks these are (PRC-13)."""
    volume: float
    open_interest: float


class Row(NamedTuple):
    expiration: int
    """The index of its expiration in the view's list of expirations."""
    strike: float
    iv: float
    """The out-of-the-money side's IV: the call at or above the forward, the put below it."""
    call: Side | None
    put: Side | None


@dataclass(frozen=True)
class ViewNote:
    subject: str
    """The underlying, or the key of an expiration."""
    kind: NoteKind
    text: str


@dataclass(frozen=True)
class Part:
    """What a view sends: a snapshot, or what changed since the part before."""

    snapshot: bool
    head: Head | None
    expirations: tuple[tuple[int, ExpirationInfo], ...]
    rows: tuple[Row, ...]
    notes: tuple[ViewNote, ...] | None
    """The whole list when it changed; None when it did not."""


@dataclass(frozen=True)
class ExpirationSnapshot:
    """A whole expiration, for the bulk route (API-06)."""

    head: Head
    expiration: ExpirationInfo
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class UnderlyingStatus:
    underlying: str
    views: int
    held: int
    """Views closed in the last grace period."""
    problem: str | None
    contracts: int
    expirations: int


@dataclass(frozen=True)
class ChainStatus:
    """For the health route (SEC-08)."""

    grace_s: float
    underlyings: tuple[UnderlyingStatus, ...]
    flushes: int
    """Flushes that had contracts due."""
    computed: int
    """Contracts computed, over all flushes."""
    largest: int
    """The most contracts of one flush."""
    compute_ms_median: float | None
    compute_ms_max: float | None
    """Of the last flushes, wall time of the call and its gathering on the event loop."""
    failed: Mapping[str, int]
    """Inversions that failed, by black76's reason."""
    errors: int
    """Unexpected errors of the flush. Each is logged, the first and then every hundredth."""


# ---------------------------------------------------------------------- the state


@dataclass(eq=False)
class _Exp:
    index: int
    key: str
    expiration: Expiration
    settles: datetime | None
    strikes: np.ndarray
    dividends: tuple[Dividend, ...]
    broker_iv: float
    # As of the last flush:
    forward: float = math.nan
    forward_source: str = NONE
    tau: float = math.nan
    df: float = math.nan
    dpv_dr: float = 0.0
    spot: float = math.nan
    proportional: bool = False
    """Of an index: its Greeks hold the implied yield fixed (s3t.pricing.carry)."""
    over: bool = False
    """From its settlement instant on."""


class _Cell:
    """One contract that a view holds, with what was computed for it last."""

    __slots__ = (
        "at",
        "ask",
        "bid",
        "exp",
        "fail",
        "forward",
        "greeks",
        "is_call",
        "iv",
        "stream",
        "strike",
        "symbol",
    )

    def __init__(self, stream: str, symbol: str, strike: float, is_call: bool, exp: _Exp) -> None:
        self.stream, self.symbol, self.strike, self.is_call, self.exp = (
            stream,
            symbol,
            strike,
            is_call,
            exp,
        )
        self.at: datetime | None = None
        self.bid = self.ask = self.forward = self.iv = math.nan
        self.fail = FAIL_OK
        self.greeks: tuple[float, float, float, float] = _NO_GREEKS


@dataclass(eq=False)
class _Resolved:
    """A window as indexes into its expiration's strikes."""

    window: Window
    exp: _Exp
    rows: tuple[int, ...]
    order: tuple[str, ...]
    """The stream symbols from the money outwards, call before put."""


@dataclass(eq=False)
class _Held:
    """A closed view's subscriptions during the grace period."""

    quotes: tuple[str, ...]
    task: asyncio.Task[None] | None = None


class _Book:
    """One underlying that views hold."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.security: Security | None = None
        self.chain: OptionChain | None = None
        self.metrics: MarketMetrics | None = None
        self.exps: list[_Exp] = []
        self.by_key: dict[str, _Exp] = {}
        self.day: date | None = None
        self.version = 0
        """Rises with every read of the documents."""
        self.problem: str | None = None
        self.unknown = False
        self.ready = asyncio.Event()
        self.watch: Watch | None = None
        self.views: list[ChainView] = []
        self.held: list[_Held] = []
        self.cells: dict[str, _Cell] = {}
        self.task: asyncio.Task[None] | None = None
        self.spot, self.spot_source = math.nan, NONE
        self.infos: list[ExpirationInfo] = []
        self.fit_number = 0
        self.slices: dict[str, Slice] = {}
        self.fit_spot = math.nan


# ---------------------------------------------------------------------- the view


class ChainView:
    """One view of one underlying. Close it when the view closes."""

    def __init__(
        self, service: "ChainService", book: _Book, windows: Sequence[Window], number: int
    ) -> None:
        self._service, self._book, self.windows = service, book, tuple(windows)
        self.number = number
        self._resolved: list[_Resolved] = []
        self._rows: list[tuple[_Exp, int]] = []
        self._at_row: dict[str, list[int]] = {}
        self._window_notes: list[ViewNote] = []
        self._version = -1
        self._event = asyncio.Event()
        self._rows_changed: set[int] = set()
        self._exps_changed: set[int] = set()
        self._head_changed = False
        self._notes: tuple[ViewNote, ...] = ()
        self._notes_changed = False
        self._again = True
        """The next part is a snapshot: the first one, or the documents were read anew."""
        self._sent: dict[str, tuple[datetime | None, bool]] = {}
        """Per contract the time of the quote last sent and its stale mark."""
        self._symbol_row: dict[str, int] = {}
        self._closed = False

    @property
    def underlying(self) -> str:
        return self._book.symbol

    @property
    def quotes(self) -> tuple[str, ...]:
        """The stream symbols of its contracts, from the money outwards, window by window."""
        return tuple(dict.fromkeys(s for r in self._resolved for s in r.order))

    async def ready(self) -> None:
        """Wait until the documents of the underlying were read, or could not be. Raises
        :class:`WindowRefused` when a window asks for more than a view may hold."""
        await self._book.ready.wait()
        self._resolve()

    def snapshot(self) -> Part:
        self._resolve()
        self._again = False
        self._rows_changed.clear()
        self._exps_changed.clear()
        self._head_changed = self._notes_changed = False
        self._event.clear()
        self._notes = self._make_notes()
        self._sent = {}
        rows = self._service._rows(self._book, self._rows)
        self._remember_stale(rows)
        book = self._book
        return Part(
            snapshot=True,
            head=self._service._head(book),
            expirations=tuple(enumerate(book.infos)),
            rows=rows,
            notes=self._notes,
        )

    async def next(self) -> Part:
        """The parts that changed since the last one, as soon as something has; a snapshot when
        the documents were read anew."""
        while True:
            if self._closed:
                raise RuntimeError("the view is closed")
            if self._book.version != self._version and self._book.ready.is_set():
                self._again = True
            if self._again:
                return self.snapshot()
            # Whatever marks a change sets its flag before the event, so a mark that comes
            # after this clear wakes the wait below.
            self._event.clear()
            self._look()
            if self._pending():
                return self._part()
            await self._service._wait(self._event, LOOK_S)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._event.set()
            self._service._close(self)

    # ------------------------------------------------------------------ inside

    def _pending(self) -> bool:
        return bool(
            self._rows_changed or self._exps_changed or self._head_changed or self._notes_changed
        )

    def _part(self) -> Part:
        book = self._book
        rows = self._service._rows(book, [self._rows[i] for i in sorted(self._rows_changed)])
        self._remember_stale(rows)
        part = Part(
            snapshot=False,
            head=self._service._head(book) if self._head_changed else None,
            expirations=tuple((i, book.infos[i]) for i in sorted(self._exps_changed)),
            rows=rows,
            notes=self._notes if self._notes_changed else None,
        )
        self._rows_changed.clear()
        self._exps_changed.clear()
        self._head_changed = self._notes_changed = False
        self._event.clear()
        return part

    def _remember_stale(self, rows: Iterable[Row]) -> None:
        for row in rows:
            for side in (row.call, row.put):
                if side is not None:
                    self._sent[side.symbol] = (side.quote_at, side.stale)

    def _look(self) -> None:
        """Stale marks that flipped without a new event, and the notes. A mark that goes off
        needs a new quote, which a flush brings with its row."""
        now = self._service._now()
        limit = timedelta(seconds=self._service._config.stale_after_s)
        for symbol, (at, stale) in self._sent.items():
            if not stale and at is not None and now - at > limit:
                row = self._symbol_row.get(symbol)
                if row is not None:
                    self._rows_changed.add(row)
        notes = self._make_notes()
        if notes != self._notes:
            self._notes, self._notes_changed = notes, True

    def _mark(self, streams: Iterable[str]) -> None:
        found = False
        for stream in streams:
            rows = self._at_row.get(stream)
            if rows:
                self._rows_changed.update(rows)
                found = True
        if found:
            self._event.set()

    def _mark_head(self) -> None:
        self._head_changed = True
        self._event.set()

    def _mark_expirations(self, indexes: Iterable[int]) -> None:
        self._exps_changed.update(indexes)
        if self._exps_changed:
            self._event.set()

    def _resolve(self) -> None:
        """The windows as rows of the expirations that the documents now list."""
        book = self._book
        if self._version == book.version:
            return
        self._version = book.version
        config = self._service._config
        resolved: list[_Resolved] = []
        notes: list[ViewNote] = []
        for w in self.windows:
            exp = book.by_key.get(w.expiration)
            if exp is None:
                if book.exps:
                    notes.append(ViewNote(w.expiration, "window", "no open expiration of this key"))
                continue
            strikes, n = exp.strikes, len(exp.strikes)
            money = exp.forward if math.isfinite(exp.forward) else book.spot
            centre = int(np.argmin(np.abs(strikes - money))) if math.isfinite(money) else n // 2
            if w.low is None and w.high is None:
                around = config.around_strikes
                lo, hi = max(0, centre - around), min(n, centre + around)
            else:
                assert w.low is not None and w.high is not None
                lo = int(np.searchsorted(strikes, w.low, "left"))
                hi = int(np.searchsorted(strikes, w.high, "right"))
                if hi - lo > config.most_strikes:
                    raise WindowRefused(
                        f"{w.expiration}: {hi - lo} strikes from {w.low:g} to {w.high:g}; "
                        f"a window holds {config.most_strikes} at most"
                    )
            rows = tuple(range(lo, hi))
            centre = min(max(centre, lo), max(hi - 1, lo))
            e = exp.expiration
            outwards = sorted(rows, key=lambda i: (abs(i - centre), i))
            order = tuple(
                s for i in outwards for s in (e.call_streams[i], e.put_streams[i]) if s is not None
            )
            resolved.append(_Resolved(w, exp, rows, order))
        self._resolved, self._window_notes = resolved, notes
        seen: dict[tuple[int, int], None] = {}
        for r in resolved:
            for k in r.rows:
                seen.setdefault((r.exp.index, k), None)
        by_index = {x.index: x for x in book.exps}
        self._rows = [(by_index[i], k) for i, k in seen]
        self._at_row, self._symbol_row = {}, {}
        for n, (exp, k) in enumerate(self._rows):
            e = exp.expiration
            for stream, symbol in ((e.call_streams[k], e.calls[k]), (e.put_streams[k], e.puts[k])):
                if stream is not None:
                    self._at_row.setdefault(stream, []).append(n)
                if symbol is not None:
                    self._symbol_row[symbol] = n
        self._service._held_changed(book)

    def _make_notes(self) -> tuple[ViewNote, ...]:
        book = self._book
        notes: list[ViewNote] = []
        if book.problem is not None:
            notes.append(
                ViewNote(book.symbol, "unknown" if book.unknown else "unreachable", book.problem)
            )
        if book.chain is not None:
            for left in book.chain.left_out:
                subject = f"{left.root or '?'}:{left.expiration_date or '?'}"
                notes.append(ViewNote(subject, "left_out", left.reason))
        notes += self._window_notes
        quotes = self.quotes
        cuts = self._service._cut
        if quotes and cuts:
            cut = sum(1 for s in quotes if Sub(s, _QUOTE) in cuts)
            if cut:
                text = f"{cut} of {len(quotes)} quotes are cut by the subscription budget"
                notes.append(ViewNote(book.symbol, "cut", text))
        return tuple(notes)


# ---------------------------------------------------------------------- the service


class ChainService:
    def __init__(
        self,
        config: ChainConfig,
        *,
        feed: Feed,
        market: MarketState,
        instruments: Instruments,
        metrics: Metrics,
        dividends: Dividends,
        surface: SurfaceService,
        calendar: ExchangeCalendar,
        curve: Callable[[datetime], RateCurve],
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._config, self._feed, self._market = config, feed, market
        self._instruments, self._metrics, self._dividends = instruments, metrics, dividends
        self._surface, self._calendar, self._curve, self._sleep = surface, calendar, curve, sleep
        self._now = market.now
        self._books: dict[str, _Book] = {}
        self._views = 0
        self._demand: Demand | None = None
        self._held_demand: Demand | None = None
        self._changes: Changes | None = None
        self._reader: Reader | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._cut: frozenset[Sub] = frozenset()
        """The pairs of the open views that the budget does not grant (SUB-03)."""
        self._flushes = self._computed = self._largest = self._errors = 0
        self._failed: Counter[int] = Counter()
        self.timings: deque[tuple[int, float, float]] = deque(maxlen=2000)
        """Per flush with contracts due: their count, the wall and the CPU seconds of the call
        and its gathering (PRF-03)."""
        limit_repeats()

    def __repr__(self) -> str:
        return f"ChainService({len(self._books)} underlyings)"

    # ------------------------------------------------------------------ for the topic and routes

    def open(self, underlying: str, windows: Sequence[Window]) -> ChainView:
        """A view of an underlying. Returns at once; ``await view.ready()`` before its
        snapshot. Raises :class:`WindowRefused` for more windows than a view may hold or a
        range that is not one."""
        if self._closed:
            raise RuntimeError("the chain service is closed")
        config = self._config
        if len(windows) > config.most_windows:
            raise WindowRefused(f"{len(windows)} windows; a view holds {config.most_windows}")
        for w in windows:
            if (w.low is None) != (w.high is None):
                raise WindowRefused(f"{w.expiration}: a range needs both ends, or neither")
            if w.low is not None and w.high is not None and not w.low <= w.high:
                raise WindowRefused(f"{w.expiration}: the range from {w.low} to {w.high}")
        book = self._books.get(underlying)
        if book is None:
            if not self._books:
                # Before the surface's watch, so that the visible rows come first in the class.
                self._demand = self._feed.consumer(CONSUMER, Priority.VIEWS)
                self._held_demand = self._feed.consumer(HELD, Priority.BACKGROUND)
                self._changes = self._market.changes(CONSUMER)
                self._start(self._take(self._changes), "chain-changes")
                self._reader = self._feed.reader(f"{CONSUMER}-cuts", events=())
                self._start(self._watch(self._reader), "chain-cuts")
            book = self._books[underlying] = _Book(underlying)
            book.watch = self._surface.watch(underlying)
            book.task = self._start(self._run(book), f"chain:{underlying}")
        self._views += 1
        view = ChainView(self, book, windows, self._views)
        book.views.insert(0, view)  # the newest first in the demand
        return view

    async def expiration(self, underlying: str, key: str) -> ExpirationSnapshot:
        """Every listed strike of one expiration, with the values the backend holds (API-06).
        It takes no subscription and no watch. Raises :class:`ChainUnavailable`."""
        book = self._books.get(underlying)
        if book is None or not book.ready.is_set() or book.chain is None:
            book = _Book(underlying)
            await self._read(book)
            if book.chain is None:
                raise ChainUnavailable(book.problem or "no chain", unknown=book.unknown)
            book.spot, book.spot_source = (
                spot_of(self._market, book.security) if book.security else (math.nan, NONE)
            )
            self._forwards(book, self._now())
            self._infos(book, self._now())
        exp = book.by_key.get(key)
        if exp is None:
            raise ChainUnavailable(f"{underlying} has no open expiration {key}", unknown=True)
        pairs = [(exp, k) for k in range(len(exp.strikes))]
        rows = self._rows(book, pairs, compute=True)
        return ExpirationSnapshot(self._head(book), book.infos[exp.index], rows)

    def status(self) -> ChainStatus:
        ms = [wall * 1000 for _, wall, _ in self.timings]
        return ChainStatus(
            grace_s=self._config.grace_s,
            underlyings=tuple(
                UnderlyingStatus(
                    underlying=b.symbol,
                    views=len(b.views),
                    held=len(b.held),
                    problem=b.problem,
                    contracts=len(b.cells),
                    expirations=len(b.exps),
                )
                for b in self._books.values()
            ),
            flushes=self._flushes,
            computed=self._computed,
            largest=self._largest,
            compute_ms_median=round(float(np.median(ms)), 3) if ms else None,
            compute_ms_max=round(max(ms), 3) if ms else None,
            failed={FAIL_NAMES[code]: n for code, n in sorted(self._failed.items())},
            errors=self._errors,
        )

    async def aclose(self) -> None:
        self._closed = True
        for book in list(self._books.values()):
            for view in list(book.views):
                view._closed = True
                view._event.set()
            book.views.clear()
            for held in book.held:
                if held.task is not None:
                    held.task.cancel()
            book.held.clear()
            self._drop(book)
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------------ views and their demand

    def _start(self, work: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(work, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _wait(self, event: asyncio.Event, seconds: float) -> None:
        """Until the event is set or the time is over, on the service's clock."""
        if event.is_set():
            return
        timer = asyncio.ensure_future(self._sleep(seconds))
        woken = asyncio.ensure_future(event.wait())
        try:
            await asyncio.wait({timer, woken}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (timer, woken):
                task.cancel()
            await asyncio.gather(timer, woken, return_exceptions=True)

    def _close(self, view: ChainView) -> None:
        book = view._book
        if view in book.views:
            book.views.remove(view)
        quotes = view.quotes
        grace = self._config.grace_s
        if quotes and grace > 0 and not self._closed:
            held = _Held(quotes)
            book.held.append(held)
            held.task = self._start(self._linger(book, held, grace), f"chain-held:{book.symbol}")
        self._held_changed(book)

    async def _linger(self, book: _Book, held: _Held, grace: float) -> None:
        try:
            await self._sleep(grace)
        finally:
            if held in book.held:
                book.held.remove(held)
                if not self._closed:
                    self._held_changed(book)

    def _held_changed(self, book: _Book) -> None:
        """The windows of a book changed: its contracts and the demand follow."""
        if not book.views and not book.held:
            self._drop(book)
            return
        self._cells(book)
        self._state_demand()

    def _drop(self, book: _Book) -> None:
        if self._books.get(book.symbol) is book:
            del self._books[book.symbol]
        if book.task is not None:
            book.task.cancel()
        if book.watch is not None:
            book.watch.release()
            book.watch = None
        book.cells.clear()
        if self._books:
            self._state_demand()
            return
        for demand in (self._demand, self._held_demand):
            if demand is not None:
                demand.close()
        self._demand = self._held_demand = None
        if self._changes is not None:
            self._changes.close()  # its task ends with it
            self._changes = None
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        self._cut = frozenset()

    def _cells(self, book: _Book) -> None:
        wanted: dict[str, None] = {}
        for view in book.views:
            wanted.update(dict.fromkeys(view.quotes))
        for held in book.held:
            wanted.update(dict.fromkeys(held.quotes))
        for stream in [s for s in book.cells if s not in wanted]:
            del book.cells[stream]
        if not wanted:
            return
        found = {
            stream: (exp, k, right)
            for exp in book.exps
            for k in range(len(exp.strikes))
            for right, stream in (
                ("call", exp.expiration.call_streams[k]),
                ("put", exp.expiration.put_streams[k]),
            )
            if stream in wanted and stream not in book.cells
        }
        for stream, (exp, k, right) in found.items():
            e = exp.expiration
            symbol = (e.calls if right == "call" else e.puts)[k] or stream
            book.cells[stream] = _Cell(stream, symbol, e.strikes[k], right == "call", exp)

    def _state_demand(self) -> None:
        if self._demand is None or self._held_demand is None:
            return
        books = list(self._books.values())
        spots = [
            Sub(b.security.stream_symbol, event)
            for b in books
            if b.security is not None
            for event in (_QUOTE, _TRADE)
        ]
        live = [s for b in books for v in b.views for s in v.quotes]
        held = [s for b in books for h in b.held for s in h.quotes]
        self._demand.set(spots + _pairs(live))
        self._held_demand.set(_pairs(held))

    async def _watch(self, reader: Reader) -> None:
        """The cuts of the budget, as the feed states them when they change."""
        async for item in reader:
            if isinstance(item, Reduction) and item.consumer == CONSUMER:
                self._cut = frozenset(item.cut)
                for book in self._books.values():
                    for view in book.views:
                        view._event.set()

    # ------------------------------------------------------------------ an underlying

    async def _run(self, book: _Book) -> None:
        while not await self._read(book):
            book.ready.set()  # the views say why
            for view in book.views:
                view._event.set()
            if book.unknown:
                return
            await self._sleep(RETRY_S)
        self._state_demand()  # the price of the underlying first
        waited, security = 0.0, book.security
        while security is not None and waited < SPOT_WAIT_S:
            if math.isfinite(spot_of(self._market, security)[0]):
                break
            await self._sleep(0.1)
            waited += 0.1
        self._update_spot(book)
        self._forwards(book, self._now())
        self._infos(book, self._now())
        book.version += 1
        book.ready.set()
        for view in book.views:
            view._event.set()
        while True:
            await self._sleep(HEAD_S)
            at = self._now()
            if at.astimezone(_NY).date() != book.day:
                if await self._read(book):  # the documents of the day before stay otherwise
                    book.version += 1
                    self._cells(book)
                    self._state_demand()
            try:
                self._flush({}, at, [book])
            except Exception:
                self._errors += 1
                if self._errors % LOG_EVERY == 1:
                    LOG.exception(
                        "chain: a look of %s failed (%d so far)", book.symbol, self._errors
                    )
            changed = self._infos(book, at)
            for view in book.views:
                if book.version != view._version:
                    view._event.set()
                else:
                    view._mark_expirations(changed)

    async def _read(self, book: _Book) -> bool:
        """Read the documents of an underlying for the day of the data. False when that failed;
        ``book.unknown`` says that another try cannot help."""
        try:
            security = await self._instruments.security(book.symbol)
            chain = await self._instruments.chain(book.symbol)
            metrics = await self._metrics.of(book.symbol)
            source = await self._dividends.of(book.symbol) if security.kind != "index" else None
        except (NotFound, ForbiddenCall):
            book.problem = "the broker knows no stock, ETF or index of this symbol"
            book.unknown = True
            return False
        except (BrokerUnavailable, RateLimited):
            book.problem = "the broker could not be asked; it is tried again"
            return False
        except BrokerError as e:
            LOG.warning("chain: the documents of %s could not be read: %s", book.symbol, e)
            book.problem, book.unknown = "the broker's answer is an error; the log has it", True
            return False
        at = self._now()
        today = at.astimezone(_NY).date()
        ivs = (
            {}
            if metrics is None
            else {(x.expiration_date, x.settlement): x.iv for x in metrics.expiration_ivs}
        )
        exps: list[_Exp] = []
        for e in chain.expirations:
            settles = settles_at(e, self._calendar) if e.family == "equity" else None
            if settles is not None and settles.astimezone(_NY).date() < today:
                continue  # over before today
            schedule = () if source is None else tuple(source.schedule(today, e.expiration_date))
            broker_iv = ivs.get((e.expiration_date, e.settlement))
            exps.append(
                _Exp(
                    index=len(exps),
                    key=f"{e.root}:{e.expiration_date.isoformat()}",
                    expiration=e,
                    settles=settles,
                    strikes=np.array(e.strikes, dtype=np.float64),
                    dividends=schedule,
                    broker_iv=math.nan if broker_iv is None else broker_iv,
                )
            )
        book.security, book.chain, book.metrics, book.day = security, chain, metrics, today
        book.exps, book.by_key = exps, {x.key: x for x in exps}
        book.problem, book.unknown = None, False
        # The cells of the documents before point at their expirations: start them anew.
        book.cells.clear()
        return True

    def _update_spot(self, book: _Book) -> bool:
        if book.security is None:
            return False
        spot, source = spot_of(self._market, book.security)
        changed = not (spot == book.spot or (math.isnan(spot) and math.isnan(book.spot)))
        book.spot, book.spot_source = spot, source
        return changed

    def _forwards(self, book: _Book, at: datetime) -> bool:
        """The forward of every open expiration, as of now. Whether a new fit came."""
        watch, fit, new = book.watch, None, False
        if watch is not None:
            fit = watch.latest
        if fit is not None and fit.number != book.fit_number:
            book.fit_number = fit.number
            book.slices = {s.key: s for s in fit.surface.slices}
            book.fit_spot = fit.spot
            new = True
        curve = self._curve(at)
        today = at.astimezone(_NY).date()
        spot = book.spot
        index = book.security is not None and book.security.kind == "index"
        for x in book.exps:
            x.spot, x.proportional = spot, index
            x.over = x.settles is None or x.settles <= at
            if x.over or not x.expiration.standard:
                x.forward, x.forward_source = math.nan, NONE
                continue
            assert x.settles is not None
            x.tau = tau_cal(at, x.settles)
            rate = float(curve.rate(x.tau))
            x.df = float(curve.discount(x.tau))
            pv = pv_dividends(x.dividends, today, rate) if x.dividends else 0.0
            x.dpv_dr = pv_dividends_dr(x.dividends, today, rate) if x.dividends else 0.0
            s = book.slices.get(x.key)
            if s is not None and math.isfinite(s.forward) and s.forward > 0:
                fit_spot = book.fit_spot
                ratio = spot / fit_spot if math.isfinite(spot) and fit_spot > 0 else 1.0
                x.forward, x.forward_source = s.forward * ratio, s.forward_source
            elif not index and math.isfinite(spot) and spot > 0:
                x.forward = float(model_forward(spot, rate, x.tau, pv))
                x.forward_source = "model"
            else:
                x.forward, x.forward_source = math.nan, NONE
        return new

    def _infos(self, book: _Book, at: datetime) -> list[int]:
        """The figures of every expiration; the indexes of those that changed."""
        earnings = self._earnings(book, at)
        instant = None if earnings is None else _report_instant(earnings[0], earnings[1])
        infos: list[ExpirationInfo] = []
        for x in book.exps:
            e = x.expiration
            s = book.slices.get(x.key)
            note = None
            if not e.standard:
                note = "an adjusted root: no forward, no own IV and no surface"
            elif x.settles is None:
                note = "the calendar gives no settlement instant"
            elif x.over:
                note = "settled: the quotes have no IV"
            elif not math.isfinite(x.forward):
                note = "no forward yet: it comes with the first surface fit"
            infos.append(
                ExpirationInfo(
                    key=x.key,
                    root=e.root,
                    expiration_date=e.expiration_date,
                    settlement=e.settlement.lower(),
                    settles_at=x.settles,
                    kind=e.kind,
                    multiplier=e.multiplier,
                    standard=e.standard,
                    strikes=len(e.strikes),
                    forward=x.forward,
                    forward_source=x.forward_source,
                    atm_iv=s.atm_iv if s is not None and not x.over else math.nan,
                    expected_move=self._expected_move(book, x),
                    expected_move_method=METHOD,
                    broker_iv=x.broker_iv,
                    ex_dividends=x.dividends,
                    earnings_before=(
                        instant is not None and x.settles is not None and instant < x.settles
                    ),
                    note=note,
                )
            )
        before = book.infos
        book.infos = infos
        if len(before) != len(infos):
            return list(range(len(infos)))
        return [i for i, (a, b) in enumerate(zip(before, infos, strict=True)) if not _same(a, b)]

    def _earnings(
        self, book: _Book, at: datetime
    ) -> tuple[date, TimeOfDay | None, bool | None] | None:
        m = book.metrics
        if m is None or m.earnings is None:
            return None
        if m.earnings.report_date < at.astimezone(_NY).date():
            return None  # the report before
        return m.earnings.report_date, m.earnings.time_of_day, m.earnings.estimated

    def _expected_move(self, book: _Book, x: _Exp) -> float:
        """From the quotes of the five strikes around the money, which the surface's core holds
        while it is watched. The money is the forward where there is one, else the spot: a
        long expiration's core lies around its forward."""
        money = x.forward if math.isfinite(x.forward) else book.spot
        if x.over or not math.isfinite(money) or not len(x.strikes):
            return math.nan
        e = x.expiration
        i = int(np.argmin(np.abs(x.strikes - money)))
        lo, hi = max(0, i - 2), min(len(x.strikes), i + 3)
        calls = [e.call_streams[k] or "" for k in range(lo, hi)]
        puts = [e.put_streams[k] or "" for k in range(lo, hi)]
        column = self._market.column
        return expected_move(
            x.strikes[lo:hi],
            column(_QUOTE, "bid", calls),
            column(_QUOTE, "ask", calls),
            column(_QUOTE, "bid", puts),
            column(_QUOTE, "ask", puts),
            money,
        )

    def _head(self, book: _Book) -> Head:
        at = self._now()
        earnings = self._earnings(book, at)
        return Head(
            underlying=book.symbol,
            kind=None if book.security is None else book.security.kind,
            spot=book.spot,
            spot_source=book.spot_source,
            at=at,
            stale_after_s=self._config.stale_after_s,
            earnings=None if earnings is None else earnings[0],
            earnings_time=None if earnings is None else earnings[1],
            earnings_estimated=None if earnings is None else earnings[2],
        )

    # ------------------------------------------------------------------ the flush

    async def _take(self, changes: Changes) -> None:
        async for flush in changes:
            try:
                self._flush(flush.changed, flush.at)
            except Exception:
                self._errors += 1
                if self._errors % LOG_EVERY == 1:
                    LOG.exception("chain: a flush failed (%d so far)", self._errors)

    def _flush(
        self,
        changed: Mapping[EventType, frozenset[str]],
        at: datetime,
        books: Sequence[_Book] | None = None,
    ) -> None:
        """What is due, in one call. A flush of the market state comes only when something
        changed, so the timer of each underlying calls this too, with nothing changed: the
        contracts that have aged, and those whose expiration has settled."""
        started, cpu = time.perf_counter(), time.thread_time()
        quotes = changed.get(_QUOTE, frozenset())
        due: list[_Cell] = []
        moved: list[_Book] = []
        settled: set[str] = set()
        held = list(self._books.values()) if books is None else books
        for book in held:
            if not book.ready.is_set() or book.security is None:
                continue
            if self._update_spot(book):
                moved.append(book)
            if self._forwards(book, at):
                # A new fit: the forwards and the IVs of the expirations at once.
                headers = self._infos(book, at)
                for view in book.views:
                    view._mark_expirations(headers)
            if not book.cells:
                continue
            streams = list(book.cells)
            bids = self._market.column(_QUOTE, "bid", streams)
            asks = self._market.column(_QUOTE, "ask", streams)
            for stream, bid, ask in zip(streams, bids, asks, strict=True):
                cell = book.cells[stream]
                x = cell.exp
                if x.over:
                    if cell.at is not None or math.isfinite(cell.iv):
                        cell.at, cell.iv, cell.greeks = None, math.nan, _NO_GREEKS
                        settled.add(stream)
                    continue
                if cell.at is None or (at - cell.at).total_seconds() >= RECOMPUTE_S:
                    due.append(cell)
                elif stream in quotes and not (
                    _same_number(bid, cell.bid) and _same_number(ask, cell.ask)
                ):
                    due.append(cell)
                elif _moved(x.forward, cell.forward):
                    due.append(cell)
        if due:
            fails = self._compute(due, at)
            self._failed.update(int(f) for f in fails[fails != FAIL_OK])
            took, used = time.perf_counter() - started, time.thread_time() - cpu
            self.timings.append((len(due), took, used))
            self._flushes += 1
            self._computed += len(due)
            self._largest = max(self._largest, len(due))
        touched = set(changed.get(_QUOTE, ())) | set(changed.get(_GREEKS, ()))
        touched |= set(changed.get(_SUMMARY, ())) | set(changed.get(_TRADE, ()))
        touched |= {c.stream for c in due} | settled
        for book in held:
            head = book in moved
            for view in book.views:
                view._mark(touched)
                if head:
                    view._mark_head()

    def _compute(self, cells: Sequence[_Cell], at: datetime) -> np.ndarray:
        """One call of the pricing core for these contracts (MKT-04). Returns the reason codes
        of the inversions."""
        streams = [c.stream for c in cells]
        column = self._market.column
        bid = np.array(column(_QUOTE, "bid", streams), dtype=np.float64)
        ask = np.array(column(_QUOTE, "ask", streams), dtype=np.float64)
        price = mids(bid, ask)
        exps = [c.exp for c in cells]
        tick = np.array(
            [
                x.expiration.ticks.tick(p) if x.expiration.ticks is not None and p == p else 0.0
                for x, p in zip(exps, price, strict=True)
            ]
        )
        values = contract_values(
            price,
            np.array([x.forward for x in exps]),
            np.array([c.strike for c in cells]),
            np.array([x.tau for x in exps]),
            np.array([x.df for x in exps]),
            np.array([x.spot for x in exps]),
            np.array([c.is_call for c in cells], dtype=bool),
            np.array([x.proportional for x in exps], dtype=bool),
            tick=tick,
            dpv_dr=np.array([x.dpv_dr for x in exps]),
        )
        units = position_units({k: values.greeks[k] for k in _GREEK_KEYS}, 1.0, 1.0)
        delta, gamma, theta, vega = (units[k] for k in _GREEK_KEYS)
        fails = values.fail
        for n, c in enumerate(cells):
            c.at, c.bid, c.ask, c.forward = at, float(bid[n]), float(ask[n]), c.exp.forward
            c.iv, c.fail = float(values.iv[n]), int(fails[n])
            c.greeks = (float(delta[n]), float(gamma[n]), float(theta[n]), float(vega[n]))
        return fails

    # ------------------------------------------------------------------ rows

    def _rows(
        self, book: _Book, pairs: Sequence[tuple[_Exp, int]], *, compute: bool = False
    ) -> tuple[Row, ...]:
        """The rows of these strikes. With ``compute``, contracts that no view holds are
        computed for this call alone (the bulk route)."""
        if not pairs:
            return ()
        cells = book.cells
        if compute:
            extra: dict[str, _Cell] = {}
            for exp, k in pairs:
                e = exp.expiration
                for right, stream, symbol in (
                    ("call", e.call_streams[k], e.calls[k]),
                    ("put", e.put_streams[k], e.puts[k]),
                ):
                    if stream is not None and stream not in cells and not exp.over:
                        extra[stream] = _Cell(
                            stream, symbol or stream, e.strikes[k], right == "call", exp
                        )
            if extra:
                self._compute(list(extra.values()), self._now())
                cells = {**cells, **extra}
        streams: list[str] = []
        for exp, k in pairs:
            e = exp.expiration
            streams += [e.call_streams[k] or "", e.put_streams[k] or ""]
        market, column = self._market, self._market.column
        bid, ask = column(_QUOTE, "bid", streams), column(_QUOTE, "ask", streams)
        bid_size, ask_size = (
            column(_QUOTE, "bid_size", streams),
            column(_QUOTE, "ask_size", streams),
        )
        volume = column(_TRADE, "day_volume", streams)
        interest = column(_SUMMARY, "open_interest", streams)
        b_iv, b_delta = column(_GREEKS, "iv", streams), column(_GREEKS, "delta", streams)
        b_gamma, b_theta = column(_GREEKS, "gamma", streams), column(_GREEKS, "theta", streams)
        b_vega = column(_GREEKS, "vega", streams)
        received = market.received(_QUOTE, streams)
        bid_at = market.instants(_QUOTE, "bid_time", streams)
        ask_at = market.instants(_QUOTE, "ask_time", streams)
        now = self._now()
        limit = timedelta(seconds=self._config.stale_after_s)
        rows: list[Row] = []
        for n, (exp, k) in enumerate(pairs):
            e = exp.expiration
            sides: list[Side | None] = []
            for j, symbol in ((2 * n, e.calls[k]), (2 * n + 1, e.puts[k])):
                stream = streams[j]
                if not stream or symbol is None:
                    sides.append(None)
                    continue
                cell = cells.get(stream)
                own_at = [t for t in (bid_at[j], ask_at[j]) if t is not None]
                quote_at = max(own_at) if own_at else received[j]
                own = cell is not None and cell.at is not None and cell.fail == FAIL_OK
                broker = math.isfinite(b_iv[j]) or math.isfinite(b_delta[j])
                if exp.over:
                    source: Source = NONE
                    values = (math.nan,) * 5
                elif own:
                    assert cell is not None
                    source, values = OWN, (cell.iv, *cell.greeks)
                elif broker:
                    source = BROKER
                    values = (b_iv[j], b_delta[j], b_gamma[j], b_theta[j], b_vega[j])
                else:
                    source, values = NONE, (math.nan,) * 5
                iv, delta, gamma, theta, vega = (_finite(v) for v in values)
                sides.append(
                    Side(
                        symbol=symbol,
                        bid=_finite(bid[j]),
                        ask=_finite(ask[j]),
                        bid_size=_finite(bid_size[j]),
                        ask_size=_finite(ask_size[j]),
                        quote_at=quote_at,
                        stale=quote_at is not None and now - quote_at > limit,
                        iv=iv,
                        delta=delta,
                        gamma=gamma,
                        theta=theta,
                        vega=vega,
                        source=source,
                        volume=_finite(volume[j]),
                        open_interest=_finite(interest[j]),
                    )
                )
            call, put = sides
            strike = e.strikes[k]
            money = exp.forward if math.isfinite(exp.forward) else book.spot
            otm = call if math.isfinite(money) and strike >= money else put
            rows.append(Row(exp.index, strike, math.nan if otm is None else otm.iv, call, put))
        return tuple(rows)


# ---------------------------------------------------------------------- helpers


def _pairs(quotes: Sequence[str]) -> list[Sub]:
    """The pairs of these contracts: all quotes first, then the Greeks, then the rest."""
    ordered = list(dict.fromkeys(quotes))
    subs = [Sub(s, _QUOTE) for s in ordered]
    subs += [Sub(s, _GREEKS) for s in ordered]
    subs += [Sub(s, event) for s in ordered for event in (_SUMMARY, _TRADE)]
    return subs


def _finite(x: float) -> float:
    return x if x - x == 0.0 else math.nan


def _same_number(a: float, b: float) -> bool:
    return a == b or (math.isnan(a) and math.isnan(b))


def _moved(now: float, then: float) -> bool:
    if math.isfinite(now) != math.isfinite(then):
        return True
    return math.isfinite(now) and abs(now / then - 1.0) >= FORWARD_MOVE


def _same(a: ExpirationInfo, b: ExpirationInfo) -> bool:
    for f in a.__dataclass_fields__:
        x, y = getattr(a, f), getattr(b, f)
        if isinstance(x, float) and isinstance(y, float):
            if not _same_number(x, y):
                return False
        elif x != y:
            return False
    return True


def _report_instant(day: date, time_of_day: TimeOfDay | None) -> datetime:
    """When a report counts as out: before the open at 09:30, after the close at 16:00, and
    at the start of the day when the time is not known, New York time."""
    clock = {"before_open": (9, 30), "after_close": (16, 0)}.get(time_of_day or "", (0, 0))
    return datetime(day.year, day.month, day.day, *clock, tzinfo=_NY)


__all__ = (
    "BROKER",
    "NONE",
    "OWN",
    "ChainService",
    "ChainStatus",
    "ChainUnavailable",
    "ChainView",
    "ExpirationInfo",
    "ExpirationSnapshot",
    "Head",
    "Part",
    "RepeatLimit",
    "Row",
    "Side",
    "ViewNote",
    "Window",
    "WindowRefused",
)
