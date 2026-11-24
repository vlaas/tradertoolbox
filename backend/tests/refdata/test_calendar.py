"""The exchange calendar (REF-04), against dates and times written by hand: what the exchanges
announced, not what the library says. Instants are given as New York wall time with their zone
and compared as instants."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from s3t.market.settlement import SessionTimes
from s3t.refdata.calendar import (
    CalendarRange,
    ExchangeCalendar,
    InstrumentClass,
    load_calendar,
)

NY = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 25)
CLASSES: tuple[InstrumentClass, ...] = ("equity", "index", "future")


@pytest.fixture(scope="module")
def cal() -> ExchangeCalendar:
    return load_calendar(TODAY)


def ny(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), NY)


def described(cal: ExchangeCalendar, day: date, of: InstrumentClass) -> list[str]:
    """``regular Fri 09:30-13:00``, in New York time."""
    return [
        f"{s.kind} {s.opens.astimezone(NY):%a %H:%M}-{s.closes.astimezone(NY):%H:%M}"
        for s in cal.sessions(day, of)
    ]


FULL_DAY = ["early 04:00-09:30", "regular 09:30-16:00", "late 16:00-20:00"]


def without_weekday(lines: list[str]) -> list[str]:
    return [f"{kind} {hours}" for kind, _, hours in (line.split(" ") for line in lines)]


# ---------------------------------------------------------------- a holiday week


def test_thanksgiving_week(cal: ExchangeCalendar) -> None:
    mon, tue, wed, thu, fri = (date(2026, 11, d) for d in range(23, 28))
    assert cal.trading_days(mon, fri) == (mon, tue, wed, fri)
    assert [cal.is_trading_day(d) for d in (wed, thu, fri)] == [True, False, True]
    assert [cal.is_early_close(d) for d in (wed, thu, fri)] == [False, False, True]
    assert cal.session(thu) is None
    assert cal.session(fri) == (ny(fri, 9, 30), ny(fri, 13))
    assert cal.next_trading_day(wed) == fri and cal.next_trading_day(thu) == fri
    assert cal.previous_trading_day(fri) == wed and cal.previous_trading_day(thu) == wed
    assert cal.next_trading_day(fri) == date(2026, 11, 30)

    assert described(cal, wed, "equity")[0] == "overnight Tue 20:00-04:00"
    assert without_weekday(described(cal, wed, "equity"))[1:] == FULL_DAY
    assert described(cal, thu, "equity") == []
    assert described(cal, fri, "equity") == [
        "overnight Thu 20:00-04:00",  # on the evening of the holiday
        "early Fri 04:00-09:30",
        "regular Fri 09:30-13:00",
        "late Fri 13:00-17:00",
    ]
    assert described(cal, fri, "index") == ["regular Fri 09:30-13:00"]
    assert described(cal, thu, "future") == ["overnight Wed 18:00-04:00", "early Thu 04:00-13:00"]
    assert described(cal, fri, "future")[-1] == "late Fri 13:00-13:15"


@pytest.mark.parametrize(
    ("at", "equity", "index", "future"),
    [
        (ny(date(2026, 11, 25), 19, 59, 59), "late", None, "overnight"),
        (ny(date(2026, 11, 25), 20), None, None, "overnight"),  # no session on Thanksgiving
        (ny(date(2026, 11, 26), 10), None, None, "early"),
        (ny(date(2026, 11, 26), 13), None, None, None),
        (ny(date(2026, 11, 26), 18), None, None, "overnight"),
        (ny(date(2026, 11, 26), 20), "overnight", None, "overnight"),  # Friday's
        (ny(date(2026, 11, 27), 12, 59, 59), "regular", "regular", "regular"),
        (ny(date(2026, 11, 27), 13), "late", None, "late"),
        (ny(date(2026, 11, 27), 13, 15), "late", None, None),
        (ny(date(2026, 11, 27), 17), None, None, None),
        (ny(date(2026, 11, 28), 12), None, None, None),  # Saturday
        (ny(date(2026, 11, 29), 17, 59, 59), None, None, None),  # Sunday
        (ny(date(2026, 11, 29), 18), None, None, "overnight"),
        (ny(date(2026, 11, 29), 20), "overnight", None, "overnight"),
    ],
)
def test_the_session_of_an_instant_in_a_holiday_week(
    cal: ExchangeCalendar, at: datetime, equity: str, index: str, future: str
) -> None:
    for of, want in zip(CLASSES, (equity, index, future), strict=True):
        found = cal.session_at(at, of)
        assert (None if found is None else found.kind) == want, of
        # The zone an instant is written in makes no difference.
        assert cal.session_at(at.astimezone(UTC), of) == found
    assert cal.market_open(at) == (equity == "regular")


def test_the_overnight_session_belongs_to_the_trading_day_after_it(cal: ExchangeCalendar) -> None:
    """Around Labor Day 2026, as measured on the broker's candles (note B)."""
    friday, monday, tuesday = date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)
    assert cal.trading_days(friday, tuesday) == (friday, tuesday)
    assert cal.session_at(ny(friday, 19, 45), "equity") is not None
    assert cal.session_at(ny(friday, 20), "equity") is None
    assert cal.session_at(ny(date(2026, 9, 6), 20), "equity") is None  # Sunday evening
    assert cal.session_at(ny(monday, 19, 45), "equity") is None
    found = cal.session_at(ny(monday, 20), "equity")
    assert found is not None and (found.kind, found.day) == ("overnight", tuesday)
    after_midnight = cal.session_at(ny(tuesday, 0), "equity")
    assert after_midnight == found


def test_christmas_and_new_year(cal: ExchangeCalendar) -> None:
    """2026: Christmas is a Friday. 2027: it is a Saturday, and the Friday before is a holiday.
    New Year 2028 is a Saturday, and the Friday before is not."""
    assert cal.trading_days(date(2026, 12, 23), date(2027, 1, 4)) == tuple(
        date(2026, 12, d) for d in (23, 24, 28, 29, 30, 31)
    ) + (date(2027, 1, 4),)
    assert cal.is_early_close(date(2026, 12, 24))
    assert not cal.is_early_close(date(2026, 12, 31))
    assert not cal.is_trading_day(date(2027, 12, 24))
    assert not cal.is_early_close(date(2027, 12, 23))
    assert cal.is_trading_day(date(2027, 12, 31))


HOLIDAYS_2026 = "01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25"
HOLIDAYS_2027 = "01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24"


@pytest.mark.parametrize(("year", "listed"), [(2026, HOLIDAYS_2026), (2027, HOLIDAYS_2027)])
def test_the_holidays_of_a_year(cal: ExchangeCalendar, year: int, listed: str) -> None:
    days = [date(year, 1, 1) + timedelta(days=n) for n in range(365)]
    closed = [f"{d:%m-%d}" for d in days if d.weekday() < 5 and not cal.is_trading_day(d)]
    assert closed == listed.split()


def test_a_national_day_of_mourning(cal: ExchangeCalendar) -> None:
    assert cal.session(date(2025, 1, 9)) is None
    assert cal.sessions(date(2025, 1, 9), "equity") == ()
    assert cal.session(date(2025, 1, 10)) is not None


# ---------------------------------------------------------------- the clock changes


@pytest.mark.parametrize(
    ("friday", "utc_open_before", "utc_open_after"),
    [
        (date(2026, 3, 6), 14, 13),  # New York goes forward on 2026-03-08
        (date(2026, 10, 30), 13, 14),  # and back on 2026-11-01
        (date(2027, 3, 12), 14, 13),
        (date(2027, 11, 5), 13, 14),
    ],
)
def test_both_clock_changes(
    cal: ExchangeCalendar, friday: date, utc_open_before: int, utc_open_after: int
) -> None:
    monday = friday + timedelta(days=3)
    for day, hour in ((friday, utc_open_before), (monday, utc_open_after)):
        opens = datetime(day.year, day.month, day.day, hour, 30, tzinfo=UTC)
        assert cal.session(day) == (opens, opens + timedelta(hours=6, minutes=30))
        # Every session keeps its New York hours and its length.
        assert without_weekday(described(cal, day, "equity"))[1:] == FULL_DAY
        lengths = [s.closes - s.opens for s in cal.sessions(day, "equity")]
        assert lengths == [timedelta(hours=h) for h in (8, 5.5, 6.5, 4)]
    assert described(cal, monday, "equity")[0] == "overnight Sun 20:00-04:00"
    # The same hour of the UTC clock lies in another session after the change.
    edge = datetime(2000, 1, 1, min(utc_open_before, utc_open_after), 45, tzinfo=UTC).timetz()
    kinds = [cal.session_at(datetime.combine(d, edge), "equity") for d in (friday, monday)]
    assert {s.kind for s in kinds if s is not None} == {"early", "regular"}


# ---------------------------------------------------------------- what always holds


def test_the_sessions_of_a_day_join_up(cal: ExchangeCalendar) -> None:
    """Each session begins where the one before it ends, all in UTC, over five years."""
    for day in cal.trading_days(date(2024, 1, 2), date(2028, 12, 29)):
        for of in CLASSES:
            sessions = cal.sessions(day, of)
            for a, b in zip(sessions, sessions[1:], strict=False):
                assert a.closes == b.opens, (day, of)
            assert all(s.opens < s.closes and s.day == day for s in sessions)
            assert all(s.opens.tzinfo is UTC and s.closes.tzinfo is UTC for s in sessions)
        regular = cal.session(day)
        assert regular is not None
        assert regular in [(s.opens, s.closes) for s in cal.sessions(day, "equity")]


def test_days_do_not_overlap(cal: ExchangeCalendar) -> None:
    days = cal.trading_days(date(2024, 1, 2), date(2028, 12, 29))
    for a, b in zip(days, days[1:], strict=False):
        for of in CLASSES:
            assert cal.sessions(a, of)[-1].closes <= cal.sessions(b, of)[0].opens, (a, b, of)


def test_the_first_and_last_instant_of_a_session(cal: ExchangeCalendar) -> None:
    day = date(2026, 11, 24)
    tick = timedelta(microseconds=1)
    for session in cal.sessions(day, "equity"):
        assert cal.session_at(session.opens, "equity") == session
        assert cal.session_at(session.closes - tick, "equity") == session
        assert cal.session_at(session.closes, "equity") != session
        assert cal.session_at(session.opens - tick, "equity") != session
    opens, closes = cal.session(day) or (None, None)
    assert opens is not None and closes is not None
    assert cal.market_open(opens) and not cal.market_open(opens - tick)
    assert cal.market_open(closes - tick) and not cal.market_open(closes)


def test_juneteenth_closes_the_futures_early(cal: ExchangeCalendar) -> None:
    """The library takes it for a full day of the futures exchange."""
    assert described(cal, date(2026, 6, 19), "future")[-1] == "early Fri 04:00-13:00"
    assert described(cal, date(2026, 4, 3), "future") == []  # Good Friday


# ---------------------------------------------------------------- the range


def test_the_range_reaches_the_last_listed_expiration(cal: ExchangeCalendar) -> None:
    status = cal.status()
    assert (status.first, status.last) == (date(1990, 1, 1), date(2033, 12, 31))
    assert status.source.startswith("exchange-calendars 4.")
    assert cal.covers(TODAY + timedelta(days=1855))  # SPX, note B section 4
    assert cal.trading_days(date(1995, 2, 7), date(1995, 2, 7))  # the first daily candle


def test_a_day_outside_the_range_is_refused(cal: ExchangeCalendar) -> None:
    before, after = date(1989, 12, 29), date(2034, 1, 3)
    for day in (before, after):
        assert not cal.covers(day)
        for ask in (
            cal.session,
            cal.is_trading_day,
            cal.is_early_close,
            cal.previous_trading_day,
            cal.next_trading_day,
            lambda d: cal.sessions(d, "equity"),
            lambda d: cal.session_at(ny(d, 12), "equity"),
            lambda d: cal.trading_days(d, TODAY),
        ):
            with pytest.raises(CalendarRange):
                ask(day)
        assert cal.market_open(ny(day, 12)) is False  # the stall rule gets an answer
    with pytest.raises(CalendarRange):
        cal.next_trading_day(date(2033, 12, 31))
    with pytest.raises(CalendarRange):
        cal.previous_trading_day(date(1990, 1, 2))
    assert cal.session_at(ny(date(2033, 12, 31), 12), "equity") is None  # the last day itself


def test_a_naive_instant_is_refused(cal: ExchangeCalendar) -> None:
    with pytest.raises(ValueError, match="aware"):
        cal.session_at(datetime(2026, 11, 25, 12), "equity")


# ---------------------------------------------------------------- configured closures


def test_a_configured_closure_closes_every_class() -> None:
    day = date(2026, 12, 9)
    cal = load_calendar(TODAY, [day])
    assert load_calendar(TODAY).is_trading_day(day)
    assert not cal.is_trading_day(day) and cal.session(day) is None
    assert all(cal.sessions(day, of) == () for of in CLASSES)
    assert cal.next_trading_day(date(2026, 12, 8)) == date(2026, 12, 10)
    assert cal.session_at(ny(date(2026, 12, 8), 21), "equity") is None
    assert not cal.market_open(ny(day, 12))
    assert cal.status().closures == (day,)
    # A day that is closed anyway, or outside the range, changes nothing.
    assert load_calendar(TODAY, [date(2026, 12, 25), date(2050, 1, 3)]).is_trading_day(day)


def test_the_calendar_is_built_once_and_can_be_built_in_a_thread() -> None:
    seen: list[ExchangeCalendar] = []

    async def build() -> None:
        seen.append(await asyncio.to_thread(load_calendar, TODAY))

    asyncio.run(build())
    assert seen[0] is load_calendar(date(2026, 1, 1))


def test_it_is_the_session_times_of_the_settlement_rule(cal: ExchangeCalendar) -> None:
    times: SessionTimes = cal
    assert times.covers(TODAY) and times.session(TODAY) is not None
