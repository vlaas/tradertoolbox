"""The instrument model (MKT-01, GEN-09): stocks, ETFs, indexes, futures and their options.

These records are part of the broker interface, like the event records of the stream. A broker
module builds them from its own documents, and no document key leaves it. Proposed 2026-11-24
(WP-0.5); the requirements, section 3, hold the reasons.

Rules for every record:

- A missing value is None, never zero and never NaN.
- An instant is an aware ``datetime`` in UTC (GEN-10). An expiration *date* is the date the
  exchange lists, a calendar date and not an instant.
- ``symbol`` is the broker's name of the instrument and ``stream_symbol`` the name its stream
  knows. Outside the broker module both are opaque keys: taken from the broker, given back to
  it, never parsed or composed.
- Values that age are not stored. The days to an expiration follow from its date and the time
  of the data.

A chain holds one :class:`Expiration` per option root and date, for equities and futures
alike. Its strikes are parallel tuples, not one object per contract: SPX has 31,158 contracts
(note B, section 4). :meth:`Expiration.contract` builds the record of one.

What the broker's chain documents leave out (note B, addendum "WP-0.5"): an equity chain
states no exercise style and no instant of expiry, so both are None there. The exercise style
of a root comes from one of its contracts (``s3t.market.registry``). The settlement instant is
a rule of the application, not a field (``s3t.market.settlement``).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

type SecurityKind = Literal["stock", "etf", "index"]
type Right = Literal["call", "put"]
type Settlement = Literal["am", "pm"]
type Exercise = Literal["american", "european"]
type Family = Literal["equity", "future"]
"""What an option is on: ``equity`` covers stocks, ETFs and indexes."""
type ExpirationKind = Literal["regular", "weekly", "quarterly", "end_of_month", "other"]


@dataclass(frozen=True, slots=True)
class TickRule:
    """The quoting tick by price. ``steps`` are (tick, below), the lowest price first: the tick
    holds for prices below ``below``. The last step has None and holds for all prices above."""

    steps: tuple[tuple[float, float | None], ...]

    def __post_init__(self) -> None:
        bounds = [below for _, below in self.steps]
        if not bounds or bounds[-1] is not None or None in bounds[:-1]:
            raise ValueError("a tick rule ends with the one step that has no bound")
        finite = [b for b in bounds if b is not None]
        if finite != sorted(set(finite)):
            raise ValueError("the bounds of a tick rule rise")
        if any(tick <= 0 for tick, _ in self.steps):
            raise ValueError("a tick is positive")

    def tick(self, price: float) -> float:
        """The tick at a price. A price at a bound takes the tick above it."""
        for tick, below in self.steps:
            if below is None or price < below:
                return tick
        raise AssertionError("unreachable: the last step has no bound")


@dataclass(frozen=True, slots=True)
class Security:
    """A stock, an ETF or an index. There is no "active" mark: the broker calls the indexes
    themselves inactive, though their options trade (note B, addendum "WP-0.5")."""

    symbol: str
    stream_symbol: str
    kind: SecurityKind
    description: str
    ticks: TickRule | None
    """None for an index, which is not quoted to be traded."""
    option_ticks: TickRule | None


@dataclass(frozen=True, slots=True)
class Future:
    """One futures contract."""

    symbol: str
    stream_symbol: str
    root: str
    """The product, for example ``/ES``."""
    description: str
    multiplier: float
    """Dollars per point of the price."""
    ticks: TickRule | None
    option_ticks: TickRule | None
    expiration_date: date
    expires_at: datetime
    stops_trading_at: datetime
    active_month: bool


@dataclass(frozen=True, slots=True)
class FutureListing:
    """A futures contract as an option chain lists it."""

    symbol: str
    stream_symbol: str
    root: str
    expiration_date: date
    expires_at: datetime
    stops_trading_at: datetime
    active_month: bool


@dataclass(frozen=True, slots=True)
class OptionContract:
    """One option contract."""

    symbol: str
    stream_symbol: str
    family: Family
    underlying: str
    """The symbol of what it delivers or settles on: the security, or the one future."""
    root: str
    expiration_date: date
    strike: float
    right: Right
    settlement: Settlement
    exercise: Exercise | None
    multiplier: float
    standard: bool
    """False for an adjusted contract, whose deliverable is not the plain multiplier."""
    expires_at: datetime | None
    """As the broker states it. For a stock or ETF option it is 15 minutes after the close
    and not the settlement instant; use :func:`s3t.market.settlement.settles_at`."""
    stops_trading_at: datetime | None


@dataclass(frozen=True, slots=True)
class Expiration:
    """One expiration of one option root, with its strikes.

    ``strikes`` rise. ``calls[i]`` and ``puts[i]`` are the symbols of the two contracts at
    ``strikes[i]``, and ``call_streams[i]`` and ``put_streams[i]`` their stream symbols; None
    where that side is not listed.
    """

    family: Family
    underlying: str
    """The symbol of the underlying of *this* expiration. It differs from one expiration to
    the next in a futures chain."""
    root: str
    expiration_date: date
    kind: ExpirationKind
    settlement: Settlement
    exercise: Exercise | None
    """None in an equity chain, which does not state it."""
    cash_settled: bool | None
    multiplier: float
    standard: bool
    ticks: TickRule | None
    expires_at: datetime | None
    """None in an equity chain. In a futures chain the settlement instant."""
    stops_trading_at: datetime | None
    strikes: tuple[float, ...]
    calls: tuple[str | None, ...]
    call_streams: tuple[str | None, ...]
    puts: tuple[str | None, ...]
    put_streams: tuple[str | None, ...]

    def __post_init__(self) -> None:
        columns = (self.calls, self.call_streams, self.puts, self.put_streams)
        if any(len(column) != len(self.strikes) for column in columns):
            raise ValueError("the columns of an expiration have one length")

    def contract(self, index: int, right: Right) -> OptionContract | None:
        """The contract at ``strikes[index]``; None where that side is not listed."""
        call = right == "call"
        symbol = (self.calls if call else self.puts)[index]
        stream = (self.call_streams if call else self.put_streams)[index]
        if symbol is None or stream is None:
            return None
        return OptionContract(
            symbol=symbol,
            stream_symbol=stream,
            family=self.family,
            underlying=self.underlying,
            root=self.root,
            expiration_date=self.expiration_date,
            strike=self.strikes[index],
            right=right,
            settlement=self.settlement,
            exercise=self.exercise,
            multiplier=self.multiplier,
            standard=self.standard,
            expires_at=self.expires_at,
            stops_trading_at=self.stops_trading_at,
        )

    def nearest(self, price: float, count: int) -> tuple[int, ...]:
        """The indexes of the ``count`` strikes nearest to a price, the nearest first."""
        order = sorted(range(len(self.strikes)), key=lambda i: abs(self.strikes[i] - price))
        return tuple(order[:count])

    def streams(self, indexes: tuple[int, ...]) -> list[str]:
        """The stream symbols at these indexes, call before put, without the ones that are
        not listed."""
        pairs = ((self.call_streams[i], self.put_streams[i]) for i in indexes)
        return [symbol for pair in pairs for symbol in pair if symbol is not None]


@dataclass(frozen=True, slots=True)
class OptionChain:
    """The expirations of every option root on one underlying or futures product."""

    underlying: str
    futures: tuple[FutureListing, ...]
    """In a futures chain, by expiration date; empty otherwise."""
    expirations: tuple[Expiration, ...]
    """By date, AM-settled before PM-settled, then by root."""

    def roots(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.root for e in self.expirations))

    def of_root(self, root: str) -> tuple[Expiration, ...]:
        return tuple(e for e in self.expirations if e.root == root)

    def find(self, root: str, expiration_date: date) -> Expiration | None:
        for e in self.expirations:
            if e.root == root and e.expiration_date == expiration_date:
                return e
        return None

    def near_the_money(
        self, price: float, *, expirations: int, strikes: int, not_before: date | None = None
    ) -> list[str]:
        """The stream symbols of calls and puts around a price: the ``strikes`` nearest
        strikes of each of the first ``expirations`` expirations, from ``not_before`` on."""
        listed = [
            e for e in self.expirations if not_before is None or e.expiration_date >= not_before
        ]
        found: list[str] = []
        for e in listed[:expirations]:
            found += e.streams(e.nearest(price, strikes))
        return found
