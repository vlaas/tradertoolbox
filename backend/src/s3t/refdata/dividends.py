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
(``s3t.market.registry``); the reads are those of ``s3t.refdata.metrics`` since WP-1.2. A read
that fails is not kept.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from s3t.broker.interface import DividendFacts
from s3t.pricing import Dividend, dividend_schedule
from s3t.refdata.metrics import Metrics


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
        self, metrics: Metrics, frequency: Mapping[str, int] | None = None, default: int = 4
    ) -> None:
        self._metrics = metrics
        self._frequency = dict(frequency or {})
        self._default = default

    def __repr__(self) -> str:
        return f"Dividends({self._metrics!r})"

    async def of(self, symbol: str) -> DividendSource:
        """The dividends of a stock or an ETF. A symbol the broker has no metrics for has
        none."""
        stated = symbol in self._frequency
        frequency = self._frequency.get(symbol, self._default)
        metrics = await self._metrics.of(symbol)
        return DividendSource(
            symbol, None if metrics is None else metrics.dividend, frequency, stated
        )
