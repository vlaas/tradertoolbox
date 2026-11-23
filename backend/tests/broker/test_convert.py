"""From dxFeed's events to the application's records (GEN-09; decided 2026-11-23): the
mapping tables, the conversions of the values and the candle symbol."""

import math
from dataclasses import fields
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from s3t.broker.interface import (
    FIELDS,
    RECORDS,
    Candle,
    CandlePeriod,
    EventBatch,
    EventType,
    Quote,
    Sub,
)
from s3t.broker.tastytrade.convert import (
    EVENT_FIELDS,
    EVENT_OF,
    SOURCES,
    WIRE_NAME,
    Decoder,
    candle_symbol,
    parse_candle_symbol,
    wire_symbol,
)

AT = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
MS = round(AT.timestamp() * 1000)


def flat(event: EventType, order: tuple[str, ...], *events: dict[str, object]) -> list[object]:
    return [e.get(name, "NaN") for e in events for name in order]


@pytest.mark.parametrize("event", list(EventType))
def test_every_field_of_a_record_has_a_source_that_is_asked_for(event: EventType) -> None:
    names = tuple(name for name, _, _ in SOURCES[event])
    assert names == FIELDS[event] == tuple(f.name for f in fields(RECORDS[event]))
    assert {wire for _, wire, _ in SOURCES[event]} <= set(EVENT_FIELDS[event])
    assert EVENT_OF[WIRE_NAME[event]] is event
    assert FIELDS[event][0] == "symbol"  # the first column of every batch


def test_the_values_of_a_quote_in_any_field_order() -> None:
    order = EVENT_FIELDS[EventType.QUOTE]
    events = (
        {"eventSymbol": "SPY", "bidPrice": 669.5, "askPrice": 669.51, "bidSize": 3, "askSize": 7,
         "bidTime": MS, "askTime": 0},
        {"eventSymbol": "SPX", "bidPrice": 6700, "askPrice": "NaN", "bidSize": "NaN",
         "askSize": None, "bidTime": "NaN", "askTime": MS + 1},
    )  # fmt: skip
    for wire_order in (order, tuple(reversed(order)), order[3:] + order[:3]):
        decoder = Decoder(EventType.QUOTE, wire_order)
        columns = decoder(flat(EventType.QUOTE, wire_order, *events))
        assert columns is not None and decoder.missing == ()
        spy, spx = EventBatch(1, EventType.QUOTE, AT, columns).rows()
        assert spy == Quote("SPY", 669.5, 669.51, 3.0, 7.0, AT, None)
        assert isinstance(spx, Quote) and spx.bid == 6700.0 and type(spx.bid) is float
        # Text, null and "no time" never cross: NaN for a number, None for a time.
        assert math.isnan(spx.ask) and math.isnan(spx.bid_size) and math.isnan(spx.ask_size)
        assert spx.bid_time is None and spx.ask_time is not None
        assert spx.ask_time.tzinfo is UTC and spx.ask_time.microsecond == 1000


def test_values_that_are_not_whole_events_are_refused() -> None:
    decoder = Decoder(EventType.TRADE, EVENT_FIELDS[EventType.TRADE])
    assert decoder(["SPY", 0, MS]) is None
    assert decoder([]) == tuple([] for _ in FIELDS[EventType.TRADE])


def test_a_field_the_server_leaves_out_is_missing_not_wrong() -> None:
    order = tuple(f for f in EVENT_FIELDS[EventType.GREEKS] if f not in ("rho", "time"))
    decoder = Decoder(EventType.GREEKS, order)
    assert set(decoder.missing) == {"rho", "time"}
    columns = decoder(flat(EventType.GREEKS, order, {"eventSymbol": ".SPY", "delta": 0.5}))
    assert columns is not None
    (row,) = EventBatch(1, EventType.GREEKS, AT, columns).rows()
    assert row.symbol == ".SPY" and row.delta == 0.5  # type: ignore[union-attr]
    assert math.isnan(row.rho) and row.time is None  # type: ignore[union-attr]
    assert Decoder(EventType.GREEKS, ("delta", "gamma"))([0.5, 0.1]) is None  # no symbol


def test_profile_status_and_candle_marks() -> None:
    order = EVENT_FIELDS[EventType.PROFILE]
    decoder = Decoder(EventType.PROFILE, order)
    statuses = ("ACTIVE", "HALTED", "UNDEFINED", None, 7)
    events = [{"eventSymbol": "X", "description": "x", "tradingStatus": s} for s in statuses]
    columns = decoder(flat(EventType.PROFILE, order, *events))
    assert columns is not None
    assert columns[2] == ["active", "halted", "unknown", "unknown", "unknown"]

    order = EVENT_FIELDS[EventType.CANDLE]
    decoder = Decoder(EventType.CANDLE, order)
    bars = [
        {"eventSymbol": "SPY{=15m}", "eventFlags": 4, "time": MS, "open": 1, "close": 2},
        {"eventSymbol": "SPY{=15m}", "eventFlags": 1, "time": MS, "open": 1, "close": 2},
        {"eventSymbol": "NOT A CANDLE", "eventFlags": 0, "time": MS},
        {"eventSymbol": "SPY{=15m}", "eventFlags": 0, "time": 0, "open": 1, "close": 2},
        {"eventSymbol": "SPY{=15m}", "eventFlags": 0, "time": "NaN", "open": 1, "close": 2},
        {"eventSymbol": "SPY{=15m}", "eventFlags": 10, "time": MS - 1000},
        {"eventSymbol": "SPY{=15m}", "eventFlags": 18, "time": MS - 2000},
    ]
    columns = decoder(flat(EventType.CANDLE, order, *bars))
    assert columns is not None
    rows = [r for r in EventBatch(1, EventType.CANDLE, AT, columns).rows() if isinstance(r, Candle)]
    # Ignored: the event whose symbol is not a candle symbol of this module, and the two
    # without a time. A candle always has its time (decided 2026-11-24).
    assert [(r.symbol, r.period) for r in rows] == [("SPY", CandlePeriod(15, "minute"))] * 4
    assert all(r.time.tzinfo is UTC for r in rows) and rows[0].time == AT
    # "Snapshot begin" (4) and "transaction pending" (1) are dropped. A snapshot ends with
    # "snapshot end" (8) or, when the history was cut, "snapshot snip" (16), each with the
    # remove flag (2).
    marks = [(r.snapshot_end, r.removed) for r in rows]
    assert marks == [(False, False), (False, False), (True, True), (True, True)]


def test_candle_symbols() -> None:
    assert candle_symbol("SPY", CandlePeriod(1, "day")) == "SPY{=d}"
    assert candle_symbol("SPY", CandlePeriod(15, "minute")) == "SPY{=15m}"
    assert candle_symbol("/ESZ6:XCME", CandlePeriod(4, "hour")) == "/ESZ6:XCME{=4h}"
    assert candle_symbol("SPX", CandlePeriod(1, "month")) == "SPX{=mo}"
    assert parse_candle_symbol("SPY{=1m}") == ("SPY", CandlePeriod(1, "minute"))
    for other in ("SPY", "SPY{=15m,tho=true}", "SPY{=d,price=mark}", "SPY{=0m}", "{=d}", "SPY{=s}"):
        assert parse_candle_symbol(other) is None, other
    day = CandlePeriod(1, "day")
    assert wire_symbol(Sub("SPY", EventType.CANDLE, day)) == "SPY{=d}"
    assert wire_symbol(Sub("SPY", EventType.QUOTE)) == "SPY"
    assert CandlePeriod.parse("15m") == CandlePeriod(15, "minute") and day.text() == "1d"
    assert CandlePeriod.parse(CandlePeriod(3, "month").text()) == CandlePeriod(3, "month")
    for wrong in ("m", "0m", "15", "15s", "1.5h"):
        with pytest.raises(ValueError):
            CandlePeriod.parse(wrong)


@given(
    st.text(st.characters(codec="ascii", exclude_characters="{}"), min_size=1, max_size=24),
    st.integers(1, 999),
    st.sampled_from(["minute", "hour", "day", "week", "month"]),
)
def test_a_candle_symbol_reads_back(symbol: str, count: int, unit: str) -> None:
    period = CandlePeriod(count, unit)  # type: ignore[arg-type]
    assert parse_candle_symbol(candle_symbol(symbol, period)) == (symbol, period)
    assert CandlePeriod.parse(period.text()) == period
