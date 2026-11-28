"""The dividend schedule from the broker's facts (REF-02): the stated payment repeated by the
frequency, read once per New York day."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest

from s3t.broker.interface import Broker, BrokerUnavailable, DividendFacts, MarketMetrics
from s3t.pricing import Dividend
from s3t.refdata.dividends import Dividends, DividendSource
from s3t.refdata.metrics import Metrics

pytestmark = pytest.mark.anyio

SPY = DividendFacts(date(2026, 9, 18), 1.83, date(2026, 10, 30))
TODAY = date(2026, 11, 27)


class Stub:
    """The two calls of a broker that the service uses."""

    def __init__(self, facts: dict[str, DividendFacts | None]) -> None:
        self.facts = facts
        self.now = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)
        self.calls: list[list[str]] = []
        self.fail = False
        self.gate: asyncio.Event | None = None

    def data_time(self) -> datetime:
        return self.now

    async def market_metrics(self, symbols: Sequence[str]) -> list[MarketMetrics]:
        self.calls.append(list(symbols))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise BrokerUnavailable("down")
        # As live: an unknown symbol has no item, and the order is the broker's own.
        return [MarketMetrics(s, self.facts[s]) for s in sorted(symbols) if s in self.facts]


def service(stub: Stub, **more: Any) -> Dividends:
    return Dividends(Metrics(cast(Broker, stub)), **more)


def test_the_last_payment_is_repeated_by_the_frequency() -> None:
    quarterly = DividendSource("SPY", SPY, 4, False)
    assert quarterly.schedule(TODAY, date(2027, 6, 30)) == [
        Dividend(date(2026, 12, 18), 1.83, True),
        Dividend(date(2027, 3, 19), 1.83, True),
        Dividend(date(2027, 6, 18), 1.83, True),
    ]
    assert quarterly.schedule(TODAY, date(2026, 12, 17)) == []
    assert quarterly.schedule(date(2026, 12, 18), date(2026, 12, 31)) == []  # ex today: gone
    monthly = DividendSource("O", DividendFacts(date(2026, 11, 2), 0.27), 12, True)
    dates = [d.ex_date for d in monthly.schedule(TODAY, date(2027, 2, 28))]
    assert dates == [date(2026, 12, 2), date(2027, 1, 1), date(2027, 1, 31)]


def test_a_date_ahead_is_a_declared_dividend() -> None:
    """Not seen in the broker's documents so far; the rule holds if it comes."""
    ahead = DividendSource("X", DividendFacts(date(2026, 12, 10), 0.5), 4, False)
    assert ahead.schedule(TODAY, date(2027, 3, 31)) == [
        Dividend(date(2026, 12, 10), 0.5, False),
        Dividend(date(2027, 3, 11), 0.5, True),
    ]


def test_no_dividend_no_schedule() -> None:
    assert DividendSource("TSLA", None, 4, False).schedule(TODAY, date(2028, 1, 1)) == []


async def test_the_frequency_is_the_configurations() -> None:
    stub = Stub({"SPY": SPY, "O": DividendFacts(date(2026, 11, 2), 0.27), "SPX": None})
    dividends = service(stub, frequency={"O": 12}, default=4)
    spy, o = await dividends.of("SPY"), await dividends.of("O")
    assert (spy.last, spy.frequency, spy.stated) == (SPY, 4, False)
    assert (o.frequency, o.stated) == (12, True)
    assert (await dividends.of("SPX")).last is None
    assert (await dividends.of("/ES")).last is None  # the broker has no metrics for it
    assert service(stub, default=2).__repr__() == "Dividends(Metrics(0 held))"


async def test_a_symbol_is_read_once_a_day() -> None:
    stub = Stub({"SPY": SPY})
    dividends = service(stub)
    stub.gate = asyncio.Event()
    waiting = [asyncio.ensure_future(dividends.of("SPY")) for _ in range(3)]
    await asyncio.sleep(0)
    waiting[0].cancel()  # one caller gives up: the read goes on for the others
    stub.gate.set()
    done = await asyncio.gather(*waiting, return_exceptions=True)
    assert isinstance(done[0], asyncio.CancelledError)
    assert isinstance(done[1], DividendSource) and done[1].last == SPY and done[1] == done[2]
    await dividends.of("SPY")
    assert stub.calls == [["SPY"]]
    stub.now = datetime(2026, 11, 28, 3, 0, tzinfo=UTC)  # 22:00 in New York: the same day
    await dividends.of("SPY")
    assert len(stub.calls) == 1
    stub.now = datetime(2026, 11, 28, 6, 0, tzinfo=UTC)  # the next day in New York
    await dividends.of("SPY")
    assert len(stub.calls) == 2


async def test_a_failed_read_is_not_kept() -> None:
    stub = Stub({"SPY": SPY})
    dividends = service(stub)
    stub.fail = True
    with pytest.raises(BrokerUnavailable):
        await dividends.of("SPY")
    stub.fail = False
    assert (await dividends.of("SPY")).last == SPY
    assert len(stub.calls) == 2
