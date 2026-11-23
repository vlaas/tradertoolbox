"""From dxFeed's events to the application's records (GEN-09; decided 2026-11-23).

No dxFeed field name and no value "as received" leaves the broker module. This file holds
the whole mapping:

- the wire name of each event type, and the field list asked of the server;
- per event, which wire field fills which field of the record, and how the value is
  converted;
- dxFeed's candle symbol, built from a stream symbol and a period, and read back.

The values of a data message are flat: one event after another, each in the field order the
server set (``FEED_CONFIG``), which need not be the order asked. :class:`Decoder` is made
for one such order and turns the flat values into the columns of an
:class:`s3t.broker.interface.EventBatch`: one slice and one pass per column.

Conversions:

- A number becomes a float. dxFeed sends a missing number as the text ``NaN`` and the
  sizes of an index quote as text; both become floats, and anything that is not a number
  becomes NaN.
- A time arrives as milliseconds since 1970 and becomes a UTC instant. 0 means "no time"
  and becomes None.
- The trading status becomes ``active``, ``halted`` or ``unknown``.
- The flags of a candle become two marks. The end of a snapshot is dxFeed's "snapshot end"
  (0x8) or "snapshot snip" (0x10): the first ends a history that is complete from the
  start asked for (seen live 2026-11-23), the second one that was cut at the server's depth
  of 8,000 bars (the owner's streamer dumps, read 2026-11-24). A removed bar is 0x2. The
  other flags are dropped: "snapshot begin" (0x4) and "transaction pending" (0x1).
- A candle event without a time, or with a symbol this module did not build, is ignored.
  The session counts such events. In 423,183 candle events of six days of the streamer's
  dumps every one had a time, the markers included.
- Wire fields without a place in a record are dropped.

The field lists are `tt`'s (tt_capture.py 1.6.0), which are the official SDK's. They were
accepted as asked on 2026-11-23 (note B, addendum "WP-0.4").
"""

import math
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

from s3t.broker.interface import FIELDS, CandlePeriod, EventType, PeriodUnit, Sub

WIRE_NAME: dict[EventType, str] = {
    EventType.QUOTE: "Quote",
    EventType.TRADE: "Trade",
    EventType.SUMMARY: "Summary",
    EventType.GREEKS: "Greeks",
    EventType.PROFILE: "Profile",
    EventType.CANDLE: "Candle",
}
EVENT_OF = {name: event for event, name in WIRE_NAME.items()}

# The fields asked per event.
EVENT_FIELDS: dict[EventType, tuple[str, ...]] = {
    EventType.QUOTE: (
        "eventSymbol", "eventTime", "sequence", "timeNanoPart", "bidTime", "bidExchangeCode",
        "askTime", "askExchangeCode", "bidPrice", "askPrice", "bidSize", "askSize",
    ),
    EventType.TRADE: (
        "eventSymbol", "eventTime", "time", "timeNanoPart", "sequence", "exchangeCode",
        "dayId", "tickDirection", "extendedTradingHours", "price", "change", "size",
        "dayVolume", "dayTurnover",
    ),
    EventType.SUMMARY: (
        "eventSymbol", "eventTime", "dayId", "dayClosePriceType", "prevDayId",
        "prevDayClosePriceType", "openInterest", "dayOpenPrice", "dayHighPrice",
        "dayLowPrice", "dayClosePrice", "prevDayClosePrice", "prevDayVolume",
    ),
    EventType.GREEKS: (
        "eventSymbol", "eventTime", "eventFlags", "index", "time", "sequence", "price",
        "volatility", "delta", "gamma", "theta", "rho", "vega",
    ),
    EventType.PROFILE: (
        "eventSymbol", "eventTime", "description", "shortSaleRestriction", "tradingStatus",
        "haltStartTime", "haltEndTime", "exDividendDayId", "statusReason",
        "high52WeekPrice", "low52WeekPrice", "beta", "shares", "highLimitPrice",
        "lowLimitPrice", "earningsPerShare", "exDividendAmount", "dividendFrequency",
        "freeFloat",
    ),
    EventType.CANDLE: (
        "eventSymbol", "eventTime", "eventFlags", "index", "time", "sequence", "count",
        "open", "high", "low", "close", "volume", "vwap", "bidVolume", "askVolume",
        "impVolatility", "openInterest",
    ),
}  # fmt: skip

SNAPSHOT_END = 0x8
SNAPSHOT_SNIP = 0x10
REMOVE_EVENT = 0x2

type Kind = Literal[
    "text", "number", "time", "status", "candle_symbol", "candle_period", "end", "removed"
]

# Per event: the record's field, the wire field it comes from, and the conversion.
SOURCES: dict[EventType, tuple[tuple[str, str, Kind], ...]] = {
    EventType.QUOTE: (
        ("symbol", "eventSymbol", "text"),
        ("bid", "bidPrice", "number"),
        ("ask", "askPrice", "number"),
        ("bid_size", "bidSize", "number"),
        ("ask_size", "askSize", "number"),
        ("bid_time", "bidTime", "time"),
        ("ask_time", "askTime", "time"),
    ),
    EventType.TRADE: (
        ("symbol", "eventSymbol", "text"),
        ("price", "price", "number"),
        ("size", "size", "number"),
        ("time", "time", "time"),
        ("day_volume", "dayVolume", "number"),
    ),
    EventType.SUMMARY: (
        ("symbol", "eventSymbol", "text"),
        ("day_open", "dayOpenPrice", "number"),
        ("day_high", "dayHighPrice", "number"),
        ("day_low", "dayLowPrice", "number"),
        ("day_close", "dayClosePrice", "number"),
        ("prev_close", "prevDayClosePrice", "number"),
        ("open_interest", "openInterest", "number"),
    ),
    EventType.GREEKS: (
        ("symbol", "eventSymbol", "text"),
        ("time", "time", "time"),
        ("price", "price", "number"),
        ("iv", "volatility", "number"),
        ("delta", "delta", "number"),
        ("gamma", "gamma", "number"),
        ("theta", "theta", "number"),
        ("rho", "rho", "number"),
        ("vega", "vega", "number"),
    ),
    EventType.PROFILE: (
        ("symbol", "eventSymbol", "text"),
        ("description", "description", "text"),
        ("trading_status", "tradingStatus", "status"),
        ("halt_start", "haltStartTime", "time"),
        ("halt_end", "haltEndTime", "time"),
        ("high_52w", "high52WeekPrice", "number"),
        ("low_52w", "low52WeekPrice", "number"),
    ),
    EventType.CANDLE: (
        ("symbol", "eventSymbol", "candle_symbol"),
        ("period", "eventSymbol", "candle_period"),
        ("time", "time", "time"),
        ("open", "open", "number"),
        ("high", "high", "number"),
        ("low", "low", "number"),
        ("close", "close", "number"),
        ("volume", "volume", "number"),
        ("vwap", "vwap", "number"),
        ("iv", "impVolatility", "number"),
        ("open_interest", "openInterest", "number"),
        ("snapshot_end", "eventFlags", "end"),
        ("removed", "eventFlags", "removed"),
    ),
}

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_STATUS = {"ACTIVE": "active", "HALTED": "halted"}

# ---------------------------------------------------------------------- candle symbols

_UNIT_LETTER: dict[PeriodUnit, str] = {
    "minute": "m",
    "hour": "h",
    "day": "d",
    "week": "w",
    "month": "mo",
}
_UNIT_OF: dict[str, PeriodUnit] = {letter: unit for unit, letter in _UNIT_LETTER.items()}
_CANDLE_SYMBOL = re.compile(r"(?P<symbol>.+)\{=(?P<count>\d*)(?P<unit>mo|m|h|d|w)\}", re.DOTALL)


def candle_symbol(symbol: str, period: CandlePeriod) -> str:
    """dxFeed's candle symbol: ``SPY{=15m}``, ``SPY{=d}``. A count of one is left out, as
    dxFeed writes it."""
    count = "" if period.count == 1 else str(period.count)
    return f"{symbol}{{={count}{_UNIT_LETTER[period.unit]}}}"


@lru_cache(maxsize=4096)
def parse_candle_symbol(text: str) -> tuple[str, CandlePeriod] | None:
    """The stream symbol and the period of a candle symbol this module built. None for
    any other form (a session or a price attribute), which the session never asks for."""
    m = _CANDLE_SYMBOL.fullmatch(text)
    if m is None or m["count"] == "0":
        return None
    return m["symbol"], CandlePeriod(int(m["count"] or 1), _UNIT_OF[m["unit"]])


def wire_symbol(sub: Sub) -> str:
    """The symbol of a subscription as the server knows it."""
    if sub.event is EventType.CANDLE and sub.period is not None:
        return candle_symbol(sub.symbol, sub.period)
    return sub.symbol


# ---------------------------------------------------------------------- values


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _time(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and value == value:
        if value != 0 and abs(value) < 1e14:
            return _EPOCH + timedelta(milliseconds=value)
    return None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _status(value: Any) -> str:
    return _STATUS.get(value, "unknown") if isinstance(value, str) else "unknown"


def _flag(mask: int) -> Callable[[Any], bool]:
    def has(value: Any) -> bool:
        return isinstance(value, int) and bool(value & mask)

    return has


def _candle_base(value: Any) -> str | None:
    parsed = parse_candle_symbol(value) if isinstance(value, str) else None
    return None if parsed is None else parsed[0]


def _candle_period(value: Any) -> CandlePeriod | None:
    parsed = parse_candle_symbol(value) if isinstance(value, str) else None
    return None if parsed is None else parsed[1]


_CONVERT: dict[Kind, Callable[[Any], Any]] = {
    "text": _text,
    "number": _number,
    "time": _time,
    "status": _status,
    "candle_symbol": _candle_base,
    "candle_period": _candle_period,
    "end": _flag(SNAPSHOT_END | SNAPSHOT_SNIP),
    "removed": _flag(REMOVE_EVENT),
}
_ABSENT: dict[Kind, Any] = {
    "text": "",
    "number": math.nan,
    "time": None,
    "status": "unknown",
    "candle_symbol": None,
    "candle_period": None,
    "end": False,
    "removed": False,
}


class Decoder:
    """Turns the flat values of one event type, in one wire field order, into columns."""

    def __init__(self, event: EventType, wire_fields: Sequence[str]) -> None:
        sources = SOURCES[event]
        if tuple(name for name, _, _ in sources) != FIELDS[event]:
            raise AssertionError(f"the sources of {event} do not match its record")
        self.event = event
        self.width = len(wire_fields)
        at = {name: i for i, name in enumerate(wire_fields)}
        self._plan: list[tuple[int | None, Kind]] = [
            (at.get(wire), kind) for _, wire, kind in sources
        ]
        self.missing = tuple(wire for _, wire, _ in sources if wire not in at)
        """Wire fields the record needs and the server does not send."""
        self.usable = "eventSymbol" in at and self.width > 0

    def __call__(self, values: Sequence[Any]) -> tuple[list[Any], ...] | None:
        """The columns, or None when the values are not whole events. A candle event
        without a time, or with a symbol this module cannot read, is left out."""
        width = self.width
        if not self.usable or len(values) % width:
            return None
        count = len(values) // width
        columns: list[list[Any]] = []
        for index, kind in self._plan:
            if index is None:
                columns.append([_ABSENT[kind]] * count)
            elif kind == "number":
                columns.append(list(map(_number, values[index::width])))
            else:
                columns.append(list(map(_CONVERT[kind], values[index::width])))
        if self.event is EventType.CANDLE and (None in columns[0] or None in columns[2]):
            keep = [
                i
                for i, (symbol, time) in enumerate(zip(columns[0], columns[2], strict=True))
                if symbol is not None and time is not None
            ]
            columns = [[column[i] for i in keep] for column in columns]
        return tuple(columns)
