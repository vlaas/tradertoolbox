"""The fake broker on holidays and early closes (WP-0.6). The dates are written by hand; what the
broker does on them comes from live documents where note B has one."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from s3t.broker.fake.broker import FakeBroker
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.fake.market import UNDERLYINGS, Root, SyntheticMarket, third_friday
from s3t.broker.fake.ticks import Ticks, market_instant
from s3t.broker.interface import EventType, StreamItem, Sub
from s3t.broker.tastytrade.dxlink import regular_hours
from s3t.config import StreamConfig
from s3t.refdata.calendar import load_calendar

from .conftest import ASOF

pytestmark = pytest.mark.anyio

THANKSGIVING, EARLY_CLOSE = date(2026, 11, 26), date(2026, 11, 27)


def market_roots() -> list[Root]:
    return [root for u in UNDERLYINGS.values() for root in u.roots]


def utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@pytest.fixture(scope="module")
def ticks(market: SyntheticMarket) -> Ticks:
    return Ticks(market)


def test_the_instants_of_an_option_on_an_early_close(market: SyntheticMarket) -> None:
    spy = market.equity_option("SPY   261127C00600000")
    spxw = market.equity_option("SPXW  261127C06000000")
    assert spy is not None and spxw is not None
    assert spy["expires-at"] == "2026-11-27T18:15:00.000+00:00"  # 13:15 New York
    assert spxw["expires-at"] == "2026-11-27T18:00:00.000+00:00"
    # Christmas week, word for word as the live document of 2026-11-22.
    christmas = market.equity_option("SPY   261224C00600000")
    assert christmas is not None
    assert christmas["expires-at"] == "2026-12-24T18:15:00.000+00:00"
    assert market.equity_option("SPY   261126C00600000") is None  # no such expiration
    assert market.equity_option("SPY   261225C00600000") is None


def test_an_am_option_after_a_holiday_trades_until_the_day_before_it(
    market: SyntheticMarket,
) -> None:
    option = market.equity_option("SPX   270617C06000000")
    assert option is not None
    assert option["expires-at"] == "2027-06-17T13:30:00.000+00:00"
    assert option["stops-trading-at"] == "2027-06-16T20:15:00.000+00:00"


def test_the_last_trading_day_of_a_month_is_its_end(market: SyntheticMarket) -> None:
    kinds = dict(market.expirations(next(r for r in market_roots() if r.symbol == "SPXW")))
    assert kinds[date(2026, 11, 30)] == "End-Of-Month"
    assert kinds[date(2026, 12, 31)] == "End-Of-Month"  # for New Year's Friday


@pytest.mark.parametrize("today", [THANKSGIVING, date(2026, 12, 25), date(2027, 6, 18)])
def test_a_market_on_a_holiday_lists_nothing_that_is_over(today: date) -> None:
    market = SyntheticMarket(utc(today, 15))
    for root in market_roots():
        listed = [day for day, _ in market.expirations(root)]
        assert listed and listed[0] > today and listed == sorted(set(listed))
        assert all(market.calendar.is_trading_day(day) for day in listed)
    monthly = dict(SyntheticMarket(utc(date(2027, 6, 14), 15)).expirations(market_roots()[0]))
    assert monthly[date(2027, 6, 17)] == "Regular"  # it wins over the daily of that Thursday


def test_the_futures_around_holidays(market: SyntheticMarket) -> None:
    lasts = dict(market._futures())
    assert lasts["/ESZ6"] == third_friday(2026, 12) and lasts["/ESM7"] == date(2027, 6, 17)
    listed = {e["contract"]: e["date"] for e in market._future_option_expirations()}
    # A weekly keeps the name of its Friday: the first of January, the fourth of December.
    assert listed["EW4Z6"] == date(2026, 12, 24) and listed["EW1F7"] == date(2026, 12, 31)
    assert listed["ESM7"] == date(2027, 6, 17)
    # Near that day the closed Friday itself gives no weekly beside the quarterly.
    june = SyntheticMarket(utc(date(2027, 6, 1), 15))._future_option_expirations()
    assert {"EW1M7", "EW2M7", "ESM7"} <= {e["contract"] for e in june}
    assert "EW3M7" not in {e["contract"] for e in june}


def test_thanksgiving_stands_still(ticks: Ticks, market: SyntheticMarket) -> None:
    closed = utc(date(2026, 11, 25), 21)  # Wednesday's close
    assert market_instant(utc(THANKSGIVING, 16), market.calendar) == closed
    symbols = ["SPY", "SPX", "/ESZ26:XCME", ".SPY261127C600"]
    for event in ("Quote", "Trade", "Greeks", "Summary"):
        before = ticks.events(event, symbols, closed)
        assert before and ticks.events(event, symbols, utc(THANKSGIVING, 16)) == before
    # Friday's previous close is Wednesday's.
    summary = ticks.events("Summary", ["SPY"], utc(EARLY_CLOSE, 16))[0]
    wednesday = ticks.events("Summary", ["SPY"], closed)[0]
    assert summary["prevDayClosePrice"] == wednesday["dayClosePrice"]
    assert summary["prevDayId"] == wednesday["dayId"]


def test_an_early_close_ends_the_session(ticks: Ticks, market: SyntheticMarket) -> None:
    close = utc(EARLY_CLOSE, 18)  # 13:00 New York
    assert market_instant(close + timedelta(hours=2), market.calendar) == close
    a, b = (ticks.events("Trade", ["SPY"], close - timedelta(minutes=m))[0] for m in (2, 1))
    assert a["price"] != b["price"]  # it moves until the close
    after = ticks.events("Summary", ["SPY"], close + timedelta(hours=2))[0]
    last = ticks.events("Trade", ["SPY"], close)[0]
    assert after["dayClosePrice"] == last["price"]
    assert ticks.price("SPY", close) == ticks.price("SPY", close + timedelta(hours=3))
    # SPY options are still there at the close; those of SPXW have expired with it.
    assert ticks.events("Quote", [".SPY261127C600"], close)
    assert not ticks.events("Quote", [".SPXW261127C6000"], close)


def test_candles_skip_a_holiday_and_end_with_an_early_close(ticks: Ticks) -> None:
    start, at = utc(date(2026, 11, 23), 0), utc(date(2026, 11, 30), 20)
    bars = ticks.candles("SPY{=30m}", start, at)[:-1]
    per_day: dict[str, list[datetime]] = {}
    for bar in reversed(bars):
        stamp = datetime.fromtimestamp(bar["time"] / 1000, UTC)
        per_day.setdefault(f"{stamp:%m-%d}", []).append(stamp)
    assert {day: len(found) for day, found in per_day.items()} == {
        "11-23": 13,
        "11-24": 13,
        "11-25": 13,
        "11-27": 7,
        "11-30": 12,  # still open
    }
    assert per_day["11-27"][-1] == utc(EARLY_CLOSE, 17, 30)
    # The daily bar of the short day agrees with them.
    daily = {b["time"]: b for b in ticks.candles("SPY{=d}", start, at)[:-1]}
    short = daily[utc(EARLY_CLOSE, 0).timestamp() * 1000]
    halves = [b for b in bars if b["time"] >= per_day["11-27"][0].timestamp() * 1000]
    halves = [b for b in halves if b["time"] <= per_day["11-27"][-1].timestamp() * 1000]
    assert short["high"] == max(b["high"] for b in halves)
    assert short["low"] == min(b["low"] for b in halves)
    assert short["volume"] == sum(b["volume"] for b in halves)
    assert utc(THANKSGIVING, 0).timestamp() * 1000 not in daily


def test_the_minute_history_is_cut_at_its_depth_also_after_holidays(ticks: Ticks) -> None:
    """Christmas and New Year lie in the weeks before. 30 days would hold too few minutes."""
    at = utc(date(2027, 1, 5), 20)
    bars = ticks.candles("SPY{=m}", at - timedelta(days=80), at)[:-1]
    assert len(bars) == 8000


def test_a_history_before_the_calendar_ends_with_it() -> None:
    """A market in 2000 whose daily history is asked back to 1980."""
    old = SyntheticMarket(utc(date(2000, 3, 6), 16))
    bars = Ticks(old).candles("SPY{=d}", utc(date(1980, 1, 1), 0), utc(date(2000, 3, 6), 16))
    assert datetime.fromtimestamp(bars[-2]["time"] / 1000, UTC).date() == date(1990, 1, 3)


def test_a_market_takes_a_calendar_with_a_configured_closure() -> None:
    calendar = load_calendar(ASOF.date(), [date(2026, 11, 24)])
    market = SyntheticMarket(ASOF, calendar=calendar)
    root = market_roots()[0]
    assert date(2026, 11, 24) not in dict(market.expirations(root))
    assert market_instant(utc(date(2026, 11, 24), 16), calendar) == utc(date(2026, 11, 23), 21)


# ---------------------------------------------------------------- the stall rule


async def silent_for_ten_minutes(
    at: datetime, data_expected: Callable[[datetime], bool] | None
) -> int:
    """Stalls seen by a fake broker that is subscribed and whose market does not move."""
    clock = VirtualClock(at)
    options: dict[str, Any] = {"stream": StreamConfig(stall_after=20.0)}
    if data_expected is not None:
        options["data_expected"] = data_expected
    broker = FakeBroker(
        clock=clock.monotonic, sleep=clock.sleep, now=clock.wall, burst=20, **options
    )
    items: list[StreamItem] = []
    session = broker.stream(items.append)
    session.start()
    for _ in range(20):
        await clock.settle()
        if session.status().phase == "connected":
            break
    await session.subscribe([Sub("SPY", EventType.QUOTE)])
    await clock.advance(600)
    stalls = session.status().stalls
    await broker.aclose()
    return stalls


@pytest.mark.parametrize(
    "at", [utc(THANKSGIVING, 15), utc(EARLY_CLOSE, 19)], ids=["holiday", "early close"]
)
async def test_the_silence_of_a_holiday_is_not_a_stall(at: datetime) -> None:
    assert regular_hours(at)  # the stand-in without a calendar expects data then
    assert await silent_for_ten_minutes(at, None) == 0
    assert await silent_for_ten_minutes(at, regular_hours) >= 1  # and would see a stall


async def test_silence_in_the_session_still_is_one() -> None:
    """The fake sends nothing by itself on this clock, so an open market is silent here too."""
    assert await silent_for_ten_minutes(utc(date(2026, 11, 25), 16), None) >= 1
