"""The dividends of a stock or an ETF, as a schedule (REF-02; product definition 11.1).

The broker states one payment: the latest ex-date it knows, usually the last one, and its cash
per share (``s3t.broker.interface.DividendFacts``). It states no payment frequency. The
schedule is that payment repeated every 365 / frequency days, the rule of
``s3t.pricing.forward.dividend_schedule``, which was taken from `volcalc`. The frequency is the
configuration's: four a year unless a symbol is named there (decided 2026-11-27, WP-1.1).

What the schedule is for. Where an expiration has quotes, its forward comes from put-call parity
and does not need a schedule (PRC-05). The schedule centres the band of strikes that parity
looks at, gives the forward where parity has too few pairs and at dates that are no expiration,
and says which expirations span an ex-date. An ex-date that is projected is an estimate: a
company moves it by days, and the amount changes.

An index and a future have no schedule: the broker states no dividend for them.

The facts of a symbol are read once per New York day of the data, as the instruments are
(``s3t.market.registry``). A read that fails is not kept.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from s3t.broker.interface import Broker, DividendFacts
from s3t.pricing import Dividend, dividend_schedule

_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DividendSource:
    """What is known of the dividends of one symbol."""

    symbol: str
    last: DividendFacts | None
    """The payment the broker states; None when it states none."""
    frequency: int
    """Payments a year that the schedule assumes."""
    stated: bool
    """Whether the configuration names the frequency of this symbol; else it is the default."""

    def schedule(self, day: date, until: date) -> list[Dividend]:
        """The ex-dates after `day` up to `until`, with their cash. A date the broker stated is
        marked as declared when it lies ahead; the repeats are marked as projected."""
        if self.last is None:
            return []
        known = [(self.last.ex_date, self.last.amount)]
        return dividend_schedule(known, self.frequency, day, until)


class Dividends:
    def __init__(
        self, broker: Broker, frequency: Mapping[str, int] | None = None, default: int = 4
    ) -> None:
        self._broker = broker
        self._frequency = dict(frequency or {})
        self._default = default
        self._held: dict[str, asyncio.Future[DividendFacts | None]] = {}
        self._day: date | None = None

    def __repr__(self) -> str:
        return f"Dividends({len(self._held)} held)"

    async def of(self, symbol: str) -> DividendSource:
        """The dividends of a stock or an ETF. A symbol the broker has no metrics for has
        none."""
        stated = symbol in self._frequency
        frequency = self._frequency.get(symbol, self._default)
        return DividendSource(symbol, await self._facts(symbol), frequency, stated)

    async def _facts(self, symbol: str) -> DividendFacts | None:
        today = self._broker.data_time().astimezone(_NY).date()
        if today != self._day:
            self._day = today
            self._held.clear()  # a read under way goes on for those who wait for it
        pending = self._held.get(symbol)
        if pending is None:
            pending = self._held[symbol] = asyncio.ensure_future(self._read(symbol))
            pending.add_done_callback(lambda done: self._drop_a_failure(symbol, done))
        return await asyncio.shield(pending)  # one caller that gives up does not end the read

    async def _read(self, symbol: str) -> DividendFacts | None:
        for metrics in await self._broker.market_metrics([symbol]):
            if metrics.symbol == symbol:
                return metrics.dividend
        return None

    def _drop_a_failure(self, symbol: str, done: asyncio.Future[DividendFacts | None]) -> None:
        failed = done.cancelled() or done.exception() is not None
        if failed and self._held.get(symbol) is done:
            del self._held[symbol]
