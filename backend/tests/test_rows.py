"""From the latest values to the columns of a wire message (MKT-07, GEN-10; market.proto). The
other language reads the same fixture in ``frontend/src/market.test.ts``."""

import json
import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.protobuf import json_format
from hypothesis import given
from hypothesis import strategies as st

from generate import QUOTE_ROWS_VALUES
from market.conftest import MarketRig, anyio_backend, market, quotes, rig, vclock  # noqa: F401
from s3t.api.rows import changed_symbols, micros, quote_rows
from s3t.broker.interface import EventBatch, EventType, Sub
from s3t.market.state import Flush
from s3t.wire.v1.market_pb2 import QuoteRows

REPO = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((REPO / "proto" / "fixtures" / "quoterows.json").read_text())
COLUMNS = [f.name for f in QuoteRows.DESCRIPTOR.fields if f.name != "symbol"]
QUOTE = EventType.QUOTE


def same(a: float, b: float) -> bool:
    return a == b or (math.isnan(a) and math.isnan(b))


def test_the_fixture_holds_its_values() -> None:
    decoded = QuoteRows.FromString(bytes.fromhex(FIXTURE["binary_hex"]))
    from_json = json_format.ParseDict(FIXTURE["json"], QuoteRows())
    for message in (decoded, from_json):
        assert list(message.symbol) == QUOTE_ROWS_VALUES["symbol"]
        for name in COLUMNS:
            got = getattr(message, name)
            assert len(got) == 2 and all(map(same, got, QUOTE_ROWS_VALUES[name])), name


def test_every_number_column_is_a_packed_double() -> None:
    """NaN is the missing value (MKT-07), so a column of any other type could not say it."""
    for f in QuoteRows.DESCRIPTOR.fields:
        if f.name != "symbol":
            assert f.type == f.TYPE_DOUBLE and f.is_repeated and f.is_packed, f.name


@pytest.mark.anyio
async def test_missing_values_reach_the_wire_as_missing(rig: MarketRig) -> None:  # noqa: F811
    """From the broker's stream to the wire message: a price of 0 without a size, an index
    without sizes, and an instrument without any value."""
    rig.dxlink.no_bid = {"SPY"}
    await rig.up()
    events = (EventType.QUOTE, EventType.TRADE, EventType.SUMMARY)
    await rig.want(*[Sub(s, e) for s in ("SPY", "SPX") for e in events])
    symbols = {"SPY": "SPY", "/ESZ6": "/ESZ26:XCME", "SPX": "SPX"}
    rows = quote_rows(rig.state, symbols)
    assert list(rows.symbol) == ["SPY", "/ESZ6", "SPX"]  # the application's, in its order
    assert {len(getattr(rows, name)) for name in COLUMNS} == {3}
    wired = QuoteRows.FromString(rows.SerializeToString())
    wired = json_format.Parse(json_format.MessageToJson(wired), QuoteRows())
    assert math.isnan(wired.bid[0]) and wired.ask[0] > 0  # no bid: missing, not zero
    assert all(math.isnan(getattr(wired, name)[1]) for name in COLUMNS)  # no value at all
    assert math.isnan(wired.bid_size[2]) and math.isnan(wired.last_size[2]) and wired.last[2] > 0
    assert wired.prev_close[0] > 0 and wired.day_volume[0] > 0
    received = rig.state.received(QUOTE, ["SPY"])[0]
    assert received is not None and wired.quote_received_us[0] == received.timestamp() * 1e6


@pytest.mark.anyio
async def test_a_zero_stays_a_zero(rig: MarketRig) -> None:  # noqa: F811
    await rig.up()
    await rig.want(Sub("SPY", QUOTE))
    await rig.publish(quotes(rig, SPY=(0.0, math.inf)))
    rows = quote_rows(rig.state, {"SPY": "SPY"})
    assert rows.bid[0] == 0.0 and math.isnan(rows.ask[0])  # no infinity on the wire


@pytest.mark.anyio
async def test_only_the_rows_that_a_flush_changed(rig: MarketRig) -> None:  # noqa: F811
    await rig.up()
    symbols = {"SPY": "SPY", "/ESZ6": "/ESZ26:XCME", "AAPL": "AAPL"}
    await rig.want(*[Sub(s, QUOTE) for s in symbols.values()])
    flushes = rig.collect(rig.state.changes("quotes"))
    await rig.publish(quotes(rig, **{"/ESZ26:XCME": (1.0, 2.0), "SPX": (3.0, 4.0)}))
    greeks = (["AAPL"], [None], *[[1.0]] * 7)
    await rig.publish(EventBatch(0, EventType.GREEKS, rig.clock.wall(), greeks))
    await rig.clock.advance(0.5)
    changed = changed_symbols(flushes[-1], symbols)
    assert changed == {"/ESZ6": "/ESZ26:XCME"}  # no Greeks in a quote row, no SPX in this list
    assert list(quote_rows(rig.state, changed).symbol) == ["/ESZ6"]
    none = Flush(rig.clock.wall(), {EventType.GREEKS: frozenset({"SPY"})})
    assert changed_symbols(none, symbols) == {} and len(quote_rows(rig.state, {}).bid) == 0


@given(st.datetimes(datetime(1990, 1, 1), datetime(2250, 1, 1), timezones=st.just(UTC)))
def test_an_instant_survives_to_the_microsecond(when: datetime) -> None:
    value = QuoteRows.FromString(
        QuoteRows(quote_received_us=[micros(when)]).SerializeToString()
    ).quote_received_us[0]
    assert datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=int(value)) == when


def test_an_instant_on_the_wire_is_utc_whatever_its_zone() -> None:
    utc = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    assert micros(utc) == 1795186800000000.0
    assert micros(utc.astimezone(timezone(timedelta(hours=-5)))) == micros(utc)
    assert math.isnan(micros(None))
    with pytest.raises(ValueError, match="aware"):
        micros(datetime(2026, 11, 20, 15, 0))
