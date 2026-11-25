"""Market values as the columns of a wire message (MKT-07, GEN-10; ``market.proto``).

A missing number stays NaN, from the broker module through the market state to the browser, and
an instant becomes microseconds since 1970 as a double. The ``quotes`` topic sends these rows
(``s3t.api.topics``).
"""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime

from s3t.broker.interface import EventType
from s3t.instants import to_micros
from s3t.market.state import Flush, MarketState
from s3t.wire.v1.market_pb2 import QuoteRows

ROW_EVENTS = (EventType.QUOTE, EventType.TRADE, EventType.SUMMARY)
"""What a row of :class:`QuoteRows` is made of."""


def micros(instant: datetime | None) -> float:
    """An instant on the wire: microseconds since 1970 UTC as a double, NaN when missing."""
    return math.nan if instant is None else float(to_micros(instant))


def _finite(values: Sequence[float]) -> list[float]:
    """A number that is not finite is missing. The broker module lets none through; this is
    the edge of the process."""
    return [x if x - x == 0.0 else math.nan for x in values]


def quote_rows(state: MarketState, symbols: Mapping[str, str]) -> QuoteRows:
    """The latest values of these instruments. ``symbols`` maps the application's symbol to
    the stream symbol, and the rows come in its order."""
    streams = list(symbols.values())

    def column(event: EventType, field: str) -> list[float]:
        return _finite(state.column(event, field, streams))

    return QuoteRows(
        symbol=list(symbols),
        bid=column(EventType.QUOTE, "bid"),
        ask=column(EventType.QUOTE, "ask"),
        bid_size=column(EventType.QUOTE, "bid_size"),
        ask_size=column(EventType.QUOTE, "ask_size"),
        last=column(EventType.TRADE, "price"),
        last_size=column(EventType.TRADE, "size"),
        day_volume=column(EventType.TRADE, "day_volume"),
        prev_close=column(EventType.SUMMARY, "prev_close"),
        quote_received_us=map(micros, state.received(EventType.QUOTE, streams)),
        trade_received_us=map(micros, state.received(EventType.TRADE, streams)),
    )


def changed_symbols(flush: Flush, symbols: Mapping[str, str]) -> dict[str, str]:
    """The instruments among ``symbols`` whose row a flush changed, in their order."""
    changed: set[str] = set()
    for event in ROW_EVENTS:
        changed |= flush.changed.get(event, frozenset())
    return {symbol: stream for symbol, stream in symbols.items() if stream in changed}
