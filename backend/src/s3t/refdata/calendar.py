"""The exchange calendar: trading days, sessions, holidays and early closes (REF-04).

Trading days and the regular session come from the `exchange-calendars` library: ``XNYS`` for
stocks, ETFs and indexes, and the trading days of ``CMES`` for futures. The hours around the
regular session are the table below; the library has none. Decided 2026-11-25 (WP-0.6).

Sessions by class of instrument, New York time. A session runs from its opening instant up to,
not including, its closing one.

- ``equity``, stocks and ETFs: overnight from 20:00 the evening before to 04:00, early to 09:30,
  regular to 16:00, late to 20:00. An early close is at 13:00, and late then ends at 17:00.
- ``index``: the regular session of the stock market only.
- ``future``, equity index futures: overnight from 18:00 the evening before to 04:00, early to
  09:30, regular as the stock market, late to 17:00. When the stock market closes early, late
  ends at 13:15. When it is closed and the futures exchange is not, there is no regular session
  and early runs to 13:00.

What is known of these hours:

- The overnight session belongs to the trading day after it. Measured on the broker's 15-minute
  candles of SPY, 2026-08-23 to 2026-11-20: no bar before Labor Day (2026-09-07) until 20:00 of
  that day, none from Friday 20:00 to Sunday 20:00, and bars in every hour between.
- Not verified: the end of late on an early close (17:00), and every holiday and early close of
  the futures. The library has 13:00 for both kinds of futures day, takes the most conservative
  hours of all CME products, and does not know that they close early on Juneteenth.
- The exchanges start their own overnight sessions on 2026-12-06, from 21:00. The feed has bars
  from 20:00 already, so the classes do not change.

Options have no class. They settle by the regular session (``s3t.market.settlement``). Their
own quoting hours differ from symbol to symbol, so a class could not state them.

The library knows holidays far ahead as rules, not as announcements. A closure that is declared
later is configured (``[calendar] closures``); it closes every class. The calendar has an end,
and an answer about a day outside it is refused, never guessed. It reaches seven years ahead,
because SPX lists options for five.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from s3t.refdata.exchange import Schedule, schedule, source

type InstrumentClass = Literal["equity", "index", "future"]
type SessionKind = Literal["overnight", "early", "regular", "late"]

NY = ZoneInfo("America/New_York")
FIRST = date(1990, 1, 1)
"""Daily candles of the broker reach back to 1995 (note C, section 11)."""
YEARS_AHEAD = 7

_DAY = timedelta(days=1)
_EQUITY_OVERNIGHT = time(20)
_FUTURE_OVERNIGHT = time(18)
_EARLY = time(4)
_REGULAR_CLOSE = time(16)
_EQUITY_LATE = timedelta(hours=4)  # 20:00 after a close at 16:00, 17:00 after one at 13:00
_FUTURE_CLOSE = time(17)
_FUTURE_EARLY_CLOSE = time(13, 15)
_FUTURE_HOLIDAY_CLOSE = time(13)


class CalendarRange(ValueError):
    """The calendar does not cover the day that was asked for."""


@dataclass(frozen=True, slots=True)
class Session:
    kind: SessionKind
    day: date
    """The trading day the session belongs to. An overnight session begins the evening before."""
    opens: datetime
    """UTC."""
    closes: datetime
    """UTC. The first instant after the session."""


@dataclass(frozen=True)
class CalendarStatus:
    """For the health route (SEC-08)."""

    first: date
    last: date
    source: str
    """The library and its version."""
    closures: tuple[date, ...]
    """The configured ones."""


class ExchangeCalendar:
    """The calendar over its range of days. It also is the ``SessionTimes`` of the settlement
    rule (``s3t.market.settlement``). Build it with :func:`load_calendar`."""

    def __init__(
        self,
        stocks: Mapping[date, tuple[datetime, datetime]],
        futures: Collection[date],
        *,
        first: date,
        last: date,
        closures: Collection[date] = (),
        source: str = "",
    ) -> None:
        closed = frozenset(closures)
        self.first, self.last = first, last
        self._stocks: Schedule = {d: s for d, s in stocks.items() if d not in closed}
        self._futures = frozenset(futures) - closed
        self._days = sorted(self._stocks)
        self._source, self._closures = source, tuple(sorted(closed))
        self._made: dict[tuple[date, InstrumentClass], tuple[Session, ...]] = {}

    def __repr__(self) -> str:
        return f"ExchangeCalendar({self.first} to {self.last})"

    def status(self) -> CalendarStatus:
        return CalendarStatus(self.first, self.last, self._source, self._closures)

    # ------------------------------------------------------------------ days

    def covers(self, day: date) -> bool:
        """Whether the calendar knows this day, with or without a session."""
        return self.first <= day <= self.last

    def is_trading_day(self, day: date) -> bool:
        """Whether the stock market has a regular session on this day."""
        return self._covered(day) in self._stocks

    def is_early_close(self, day: date) -> bool:
        session = self.session(day)
        return session is not None and session[1].astimezone(NY).time() < _REGULAR_CLOSE

    def previous_trading_day(self, day: date) -> date:
        """The last trading day before this day, which need not be one."""
        i = bisect_left(self._days, self._covered(day))
        if i == 0:
            raise CalendarRange(f"no trading day before {day} in {self!r}")
        return self._days[i - 1]

    def next_trading_day(self, day: date) -> date:
        """The first trading day after this day, which need not be one."""
        i = bisect_right(self._days, self._covered(day))
        if i == len(self._days):
            raise CalendarRange(f"no trading day after {day} in {self!r}")
        return self._days[i]

    def trading_days(self, first: date, last: date) -> tuple[date, ...]:
        """The trading days from ``first`` to ``last``, both included."""
        low, high = self._covered(first), self._covered(last)
        return tuple(self._days[bisect_left(self._days, low) : bisect_right(self._days, high)])

    # ------------------------------------------------------------------ sessions

    def session(self, day: date) -> tuple[datetime, datetime] | None:
        """The open and the close of the stock market's regular session, in UTC; None on a day
        without one."""
        return self._stocks.get(self._covered(day))

    def sessions(self, day: date, of: InstrumentClass) -> tuple[Session, ...]:
        """The sessions of a trading day in time order, the overnight session of the evening
        before included. Empty on a day without any."""
        key = (self._covered(day), of)
        if key not in self._made:
            self._made[key] = self._make(day, of)
        return self._made[key]

    def session_at(self, instant: datetime, of: InstrumentClass) -> Session | None:
        """The session an instant lies in; None when the market of this class is closed."""
        if instant.tzinfo is None:
            raise ValueError("an instant is aware (GEN-10)")
        day = self._covered(instant.astimezone(NY).date())
        for near in (day, day + _DAY):
            if self.covers(near):
                for session in self.sessions(near, of):
                    if session.opens <= instant < session.closes:
                        return session
        return None

    def market_open(self, instant: datetime) -> bool:
        """Whether the stock market is in its regular session. Answers for every instant: one
        outside the calendar counts as closed. For the stall rule (BRK-03)."""
        day = instant.astimezone(NY).date()
        session = self.session(day) if self.covers(day) else None
        return session is not None and session[0] <= instant < session[1]

    def _make(self, day: date, of: InstrumentClass) -> tuple[Session, ...]:
        regular = self._stocks.get(day)
        if of == "future":
            return self._make_future(day, regular) if day in self._futures else ()
        if regular is None:
            return ()
        opens, closes = regular
        if of == "index":
            return (Session("regular", day, opens, closes),)
        early = _at(day, _EARLY)
        return (
            Session("overnight", day, _at(day - _DAY, _EQUITY_OVERNIGHT), early),
            Session("early", day, early, opens),
            Session("regular", day, opens, closes),
            Session("late", day, closes, closes + _EQUITY_LATE),
        )

    def _make_future(
        self, day: date, regular: tuple[datetime, datetime] | None
    ) -> tuple[Session, ...]:
        early = _at(day, _EARLY)
        overnight = Session("overnight", day, _at(day - _DAY, _FUTURE_OVERNIGHT), early)
        if regular is None:
            return overnight, Session("early", day, early, _at(day, _FUTURE_HOLIDAY_CLOSE))
        opens, closes = regular
        ends = _at(day, _FUTURE_EARLY_CLOSE if self.is_early_close(day) else _FUTURE_CLOSE)
        return (
            overnight,
            Session("early", day, early, opens),
            Session("regular", day, opens, closes),
            Session("late", day, closes, ends),
        )

    def _covered(self, day: date) -> date:
        if not self.covers(day):
            raise CalendarRange(f"{day} is outside the calendar, {self.first} to {self.last}")
        return day


def _at(day: date, at: time) -> datetime:
    return datetime.combine(day, at, NY).astimezone(UTC)


def load_calendar(today: date, closures: Collection[date] = ()) -> ExchangeCalendar:
    """The calendar from 1990 to the end of the seventh year after ``today``. Blocking: the
    library takes its time, so a coroutine calls it in a thread. The result is kept."""
    return _load(date(today.year + YEARS_AHEAD, 12, 31), tuple(sorted(closures)))


@lru_cache(maxsize=4)
def _load(last: date, closures: tuple[date, ...]) -> ExchangeCalendar:
    return ExchangeCalendar(
        schedule("XNYS", FIRST, last),
        schedule("CMES", FIRST, last).keys(),
        first=FIRST,
        last=last,
        closures=closures,
        source=source(),
    )
