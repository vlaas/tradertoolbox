"""The settlement instant (REF-05, MKT-01), on a calendar of the test's own. The exchange
calendar comes with WP-0.6."""

import dataclasses
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.instruments import Expiration
from s3t.broker.tastytrade.instruments import (
    parse_equity_option,
    parse_futures_option_chain,
    parse_option_chain,
)
from s3t.market.settlement import settles_at
from s3t.pricing import tau_cal

NY = ZoneInfo("America/New_York")
THANKSGIVING, EARLY_CLOSE = date(2026, 11, 26), date(2026, 11, 27)


class Sessions:
    """09:30 to 16:00 New York on weekdays; closed on Thanksgiving and until 13:00 the day
    after it."""

    def session(self, day: date) -> tuple[datetime, datetime] | None:
        if day.weekday() >= 5 or day == THANKSGIVING:
            return None
        closes = time(13) if day == EARLY_CLOSE else time(16)
        opened, closed = (datetime.combine(day, t, NY) for t in (time(9, 30), closes))
        return opened.astimezone(UTC), closed.astimezone(UTC)


@pytest.fixture
def spx(market: SyntheticMarket) -> list[Expiration]:
    doc = market.option_chain("SPX")
    assert doc is not None
    return list(parse_option_chain(doc, "SPX").expirations)


def at(expiration: Expiration, **changes: object) -> datetime | None:
    return settles_at(dataclasses.replace(expiration, **changes), Sessions())  # type: ignore[arg-type]


def test_am_settles_at_the_open_and_pm_at_the_close(spx: list[Expiration]) -> None:
    day = date(2026, 12, 18)
    am, pm = (e for e in spx if e.expiration_date == day)
    assert settles_at(am, Sessions()) == datetime(2026, 12, 18, 14, 30, tzinfo=UTC)
    assert settles_at(pm, Sessions()) == datetime(2026, 12, 18, 21, 0, tzinfo=UTC)
    instant = settles_at(pm, Sessions())
    assert instant is not None and instant.tzinfo is UTC
    assert tau_cal(datetime(2026, 12, 17, 21, 0, tzinfo=UTC), instant) == pytest.approx(1 / 365)


def test_an_early_close_a_holiday_and_the_clock_change(spx: list[Expiration]) -> None:
    pm = next(e for e in spx if e.settlement == "pm")
    assert at(pm, expiration_date=EARLY_CLOSE) == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert at(pm, expiration_date=THANKSGIVING) is None  # no session: no guess
    # New York puts its clocks forward on 2027-03-14.
    assert at(pm, expiration_date=date(2027, 3, 12)) == datetime(2027, 3, 12, 21, 0, tzinfo=UTC)
    assert at(pm, expiration_date=date(2027, 3, 15)) == datetime(2027, 3, 15, 20, 0, tzinfo=UTC)


def test_the_brokers_instant_is_not_used_for_an_equity_option(market: SyntheticMarket) -> None:
    """It is 15 minutes after the close (note B, addendum "WP-0.5")."""
    doc = market.equity_option("SPY   261218C00600000")
    assert doc is not None
    option = parse_equity_option(doc)
    assert option.expires_at == datetime(2026, 12, 18, 21, 15, tzinfo=UTC)
    assert settles_at(option, Sessions()) == datetime(2026, 12, 18, 21, 0, tzinfo=UTC)


def test_a_futures_option_settles_when_the_broker_says(market: SyntheticMarket) -> None:
    doc = market.futures_option_chain("ES")
    assert doc is not None
    chain = parse_futures_option_chain(doc, "/ES")
    for expiration in chain.expirations:
        assert settles_at(expiration, Sessions()) == expiration.expires_at
    weekly = chain.of_root("EW4")[0]
    assert settles_at(weekly, Sessions()) == datetime(2026, 11, 27, 21, 0, tzinfo=UTC)
