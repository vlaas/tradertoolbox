"""The instrument model and the reading of the broker's documents (MKT-01, GEN-09).

The documents are the fake's, whose keys were compared with the live ones, and changed copies
of them for what the fake does not show. Facts behind them: note B, addendum "WP-0.5".
"""

import copy
import dataclasses
import logging
from datetime import UTC, date, datetime
from typing import Any, get_type_hints

import pytest
from hypothesis import given
from hypothesis import strategies as st

from s3t.broker import instruments
from s3t.broker.fake.market import SyntheticMarket, third_friday
from s3t.broker.instruments import OptionChain, TickRule
from s3t.broker.interface import BrokerProtocolError
from s3t.broker.recording import RESULTS, decode_result, encode_result
from s3t.broker.tastytrade.instruments import (
    parse_equity_option,
    parse_future,
    parse_future_option,
    parse_futures_option_chain,
    parse_option_chain,
    parse_security,
)

TODAY = date(2026, 11, 20)


def doc_of(found: dict[str, Any] | None) -> dict[str, Any]:
    assert found is not None
    return copy.deepcopy(found)


@pytest.fixture
def spx(market: SyntheticMarket) -> dict[str, Any]:
    return doc_of(market.option_chain("SPX"))


@pytest.fixture
def es(market: SyntheticMarket) -> dict[str, Any]:
    return doc_of(market.futures_option_chain("ES"))


# ---------------------------------------------------------------------- securities, futures


def test_securities(market: SyntheticMarket) -> None:
    spy, spx, aapl = (parse_security(doc_of(market.equity(s))) for s in ("SPY", "SPX", "AAPL"))
    assert (spy.kind, spx.kind, aapl.kind) == ("etf", "index", "stock")
    assert spy.ticks is not None and (spy.ticks.tick(0.5), spy.ticks.tick(600.0)) == (0.0001, 0.01)
    # An index is not quoted to be traded, and the broker calls it inactive; its options are live.
    assert spx.ticks is None and doc_of(market.equity("SPX"))["active"] is False
    assert spx.option_ticks is not None and spx.stream_symbol == "SPX"
    assert aapl.option_ticks is not None
    assert (aapl.option_ticks.tick(2.99), aapl.option_ticks.tick(3.0)) == (0.01, 0.05)


def test_a_future(market: SyntheticMarket) -> None:
    future = parse_future(doc_of(market.future("/ESZ6")))
    assert (future.symbol, future.root, future.stream_symbol) == ("/ESZ6", "/ES", "/ESZ26:XCME")
    assert future.multiplier == 50.0 and future.ticks == TickRule(((0.25, None),))
    assert future.expiration_date == third_friday(2026, 12)
    assert future.expires_at == datetime(2026, 12, 18, 14, 30, tzinfo=UTC)
    assert future.expires_at.tzinfo is UTC


# ---------------------------------------------------------------------- chains


def test_the_spy_chain(market: SyntheticMarket) -> None:
    chain = parse_option_chain(doc_of(market.option_chain("SPY")), "SPY")
    assert chain.roots() == ("SPY",) and chain.futures == ()
    dates = [e.expiration_date for e in chain.expirations]
    assert dates == sorted(dates) and dates[0] == TODAY
    first = chain.expirations[0]
    assert (first.family, first.underlying, first.settlement) == ("equity", "SPY", "pm")
    assert (first.multiplier, first.standard, first.cash_settled) == (100.0, True, False)
    # What an equity chain does not state is missing, not guessed.
    assert (first.exercise, first.expires_at, first.stops_trading_at) == (None, None, None)
    assert list(first.strikes) == sorted(first.strikes) and 588.5 in first.strikes
    call = first.contract(first.strikes.index(588.5), "call")
    assert call is not None and call.symbol == "SPY   261120C00588500"
    assert (call.stream_symbol, call.strike, call.right) == (".SPY261120C588.5", 588.5, "call")


def test_the_spx_chain_has_several_roots_with_their_own_settlement(spx: dict[str, Any]) -> None:
    chain = parse_option_chain(spx, "SPX")
    assert set(chain.roots()) == {"SPXW", "SPXO", "SPX"}
    assert {e.settlement for e in chain.of_root("SPX")} == {"am"}
    assert {e.settlement for e in chain.of_root("SPXO")} == {"am"}
    assert {e.settlement for e in chain.of_root("SPXW")} == {"pm"}
    assert all(e.cash_settled is True for e in chain.expirations)
    # On the third Friday both settle, the morning one first.
    day = third_friday(2026, 12)
    both = [e for e in chain.expirations if e.expiration_date == day]
    assert [(e.root, e.settlement) for e in both] == [("SPX", "am"), ("SPXW", "pm")]
    assert chain.find("SPX", day) is both[0] and chain.find("SPX", date(2026, 12, 17)) is None
    kinds = {e.kind for e in chain.of_root("SPXW")}
    assert kinds == {"regular", "weekly", "end_of_month"}
    assert chain.expirations[0].ticks == TickRule(((0.05, 3.0), (0.1, None)))


def test_a_futures_option_root(es: dict[str, Any]) -> None:
    listed = [f["symbol"] for f in es["futures"]]
    assert listed == ["/ESM7", "/ESH7", "/ESZ6"]  # not in date order, as live
    chain = parse_futures_option_chain(es, "/ES")
    assert [f.symbol for f in chain.futures] == ["/ESZ6", "/ESH7", "/ESM7"]
    weekly, quarterly = chain.of_root("EW4")[0], chain.of_root("ES")
    assert (weekly.family, weekly.underlying, weekly.settlement) == ("future", "/ESZ6", "pm")
    # Each expiration has its own underlying, and the future behind it is in the chain.
    assert [e.underlying for e in quarterly] == ["/ESZ6", "/ESH7", "/ESM7"]
    streams = {f.symbol: f.stream_symbol for f in chain.futures}
    assert streams[weekly.underlying] == "/ESZ26:XCME"
    assert weekly.multiplier == 50.0  # notional-value / display-factor, as the future's own
    assert weekly.exercise == "american" and weekly.cash_settled is False
    assert weekly.expires_at == datetime(2026, 11, 27, 21, 0, tzinfo=UTC)  # 15:00 Chicago
    assert quarterly[0].settlement == "am" and quarterly[0].kind == "regular"
    assert weekly.ticks is not None and weekly.ticks.tick(12.0) == 0.25


def test_contracts_read_alone(market: SyntheticMarket, es: dict[str, Any]) -> None:
    option = parse_equity_option(doc_of(market.equity_option("SPX   261218P06000000")))
    assert (option.root, option.right, option.settlement) == ("SPX", "put", "am")
    assert (option.exercise, option.multiplier, option.standard) == ("european", 100.0, True)
    # It settles on Friday morning and trades until Thursday afternoon, as live.
    assert option.expires_at == datetime(2026, 12, 18, 14, 30, tzinfo=UTC)
    assert option.stops_trading_at == datetime(2026, 12, 17, 21, 15, tzinfo=UTC)
    aapl = parse_equity_option(doc_of(market.equity_option("AAPL  261218C00200000")))
    # The broker's instant for a stock option is 15 minutes after the close, as live.
    assert aapl.expires_at == datetime(2026, 12, 18, 21, 15, tzinfo=UTC)
    listed = parse_futures_option_chain(es, "/ES").of_root("ES")[0].contract(5, "put")
    assert listed is not None
    assert parse_future_option(doc_of(market.future_option(listed.symbol))) == listed


def test_a_futures_option_read_alone_says_am_or_pm_in_another_key(
    market: SyntheticMarket, es: dict[str, Any]
) -> None:
    """Live, 2026-11-23: its ``settlement-type`` is neither AM nor PM, unlike the chain's."""
    chain = parse_futures_option_chain(es, "/ES")
    for root, settlement in (("ES", "am"), ("EW4", "pm")):
        listed = chain.of_root(root)[0].contract(5, "call")
        assert listed is not None
        doc = doc_of(market.future_option(listed.symbol))
        assert doc["settlement-type"] not in ("AM", "PM")
        assert parse_future_option(doc).settlement == settlement
        del doc["future-option-product"]
        with pytest.raises(BrokerProtocolError, match="future option: settlement-type is"):
            parse_future_option(doc)
        doc["settlement-type"] = settlement.upper()
        assert parse_future_option(doc).settlement == settlement


# ---------------------------------------------------------------------- odd documents


def test_an_adjusted_root_is_marked(spx: dict[str, Any]) -> None:
    adjusted = copy.deepcopy(spx["items"][0])
    adjusted.update({"root-symbol": "SPXW1", "option-chain-type": "Non-standard"})
    adjusted["shares-per-contract"] = 133
    adjusted["deliverables"] = [{"deliverable-type": "Shares"}, {"deliverable-type": "Cash"}]
    spx["items"].append(adjusted)
    chain = parse_option_chain(spx, "SPX")
    odd, plain = chain.of_root("SPXW1")[0], chain.of_root("SPXW")[0]
    assert (odd.standard, odd.multiplier, odd.cash_settled) == (False, 133.0, False)
    assert (plain.standard, plain.multiplier, plain.cash_settled) == (True, 100.0, True)


def test_a_side_that_is_not_listed_is_missing(spx: dict[str, Any]) -> None:
    rows = spx["items"][0]["expirations"][0]["strikes"]
    del rows[3]["put"]
    rows[4]["call-streamer-symbol"] = ""
    rows.reverse()  # the order of the document does not matter
    found = parse_option_chain(spx, "SPX").of_root("SPXW")[0]
    assert found.contract(3, "put") is None and found.contract(3, "call") is not None
    assert found.contract(4, "call") is None and found.contract(4, "put") is not None
    assert list(found.strikes) == sorted(found.strikes)
    assert len(found.streams((3, 4, 5))) == 5  # the side without a stream symbol is left out
    assert found.nearest(found.strikes[7] + 0.1, 2)[0] == 7


@pytest.mark.parametrize(
    ("key", "value", "problem"),
    [
        ("settlement-type", "XM", "settlement-type is unreadable"),
        ("settlement-type", None, "settlement-type is missing"),
        ("expiration-date", "soon", "expiration-date is unreadable"),
        ("strikes", None, "strikes is missing"),
        ("strikes", [{"strike-price": "NaN"}], "strike-price is unreadable"),
        ("strikes", [{"strike-price": "-5"}], "strike-price is unreadable"),
    ],
)
def test_an_expiration_that_cannot_be_read_is_left_out_and_logged(
    spx: dict[str, Any], caplog: pytest.LogCaptureFixture, key: str, value: Any, problem: str
) -> None:
    whole = parse_option_chain(copy.deepcopy(spx), "SPX")
    spx["items"][0]["expirations"][1][key] = value
    with caplog.at_level(logging.WARNING):
        chain = parse_option_chain(spx, "SPX")
    assert len(chain.expirations) == len(whole.expirations) - 1
    assert "left out" in caplog.text and problem in caplog.text


def test_a_chain_of_which_nothing_can_be_read_is_an_error(spx: dict[str, Any]) -> None:
    for item in spx["items"]:
        for e in item["expirations"]:
            del e["settlement-type"]
    with pytest.raises(BrokerProtocolError, match="option chain SPX: none of its expirations"):
        parse_option_chain(spx, "SPX")
    with pytest.raises(BrokerProtocolError, match="option chain SPX: items is missing"):
        parse_option_chain({}, "SPX")
    assert parse_option_chain({"items": []}, "X") == OptionChain("X", (), ())


def test_a_strike_factor_that_is_not_understood(es: dict[str, Any]) -> None:
    """Better no expiration than one with wrong strikes."""
    expirations = es["option-chains"][0]["expirations"]
    expirations[0]["strike-factor"] = "0.01"
    chain = parse_futures_option_chain(es, "/ES")
    assert len(chain.expirations) == len(expirations) - 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("expires-at", "2026-12-18T21:00:00"),  # an instant without a zone is not an instant
        ("expires-at", "tomorrow"),
        ("strike-price", "Infinity"),
        ("exercise-style", "Bermudan"),
        ("shares-per-contract", 0),
        ("streamer-symbol", ""),
        ("option-type", None),
    ],
)
def test_the_error_names_the_key_and_not_the_value(
    market: SyntheticMarket, key: str, value: Any
) -> None:
    doc = doc_of(market.equity_option("SPX   261218P06000000"))
    doc[key] = value
    with pytest.raises(BrokerProtocolError, match=f"equity option: {key} is") as info:
        parse_equity_option(doc)
    assert not value or str(value) not in str(info.value)


def test_an_offset_is_brought_to_utc(market: SyntheticMarket) -> None:
    doc = doc_of(market.equity_option("SPX   261218P06000000"))
    doc["expires-at"] = "2026-12-18T09:30:00-05:00"
    option = parse_equity_option(doc)
    assert option.expires_at == datetime(2026, 12, 18, 14, 30, tzinfo=UTC)
    assert option.expires_at is not None and option.expires_at.tzinfo is UTC


# ---------------------------------------------------------------------- tick rules


@pytest.mark.parametrize(
    "steps",
    [
        (),
        ((0.01, 3.0),),  # no step without a bound
        ((0.01, None), (0.05, None)),
        ((0.01, 5.0), (0.05, 3.0), (0.1, None)),
        ((0.01, 3.0), (0.05, 3.0), (0.1, None)),
        ((0.0, None),),
    ],
)
def test_a_wrong_tick_rule_is_refused(steps: tuple[tuple[float, float | None], ...]) -> None:
    with pytest.raises(ValueError):
        TickRule(steps)


def test_a_tick_rule_that_cannot_be_read_is_missing(market: SyntheticMarket) -> None:
    doc = doc_of(market.equity("SPY"))
    for value in ([], "0.01", [{"value": "x"}], [{"threshold": "3.0", "value": "0.01"}], [7]):
        doc["option-tick-sizes"] = value
        assert parse_security(doc).option_ticks is None
    doc["option-tick-sizes"] = [{"value": "0.1"}, {"value": "0.05", "threshold": "3.0"}]
    assert parse_security(doc).option_ticks == TickRule(((0.05, 3.0), (0.1, None)))  # sorted


@given(
    st.lists(st.floats(0.01, 1e4), max_size=4, unique=True).map(sorted),
    st.floats(0, 2e4, allow_nan=False),
)
def test_the_tick_is_the_one_of_the_first_bound_above(bounds: list[float], price: float) -> None:
    steps = [(0.01 * (i + 1), bound) for i, bound in enumerate([*bounds, None])]
    below = sum(1 for b in bounds if b <= price)  # a price at a bound takes the tick above it
    assert TickRule(tuple(steps)).tick(price) == 0.01 * (below + 1)


# ---------------------------------------------------------------------- the records


def test_every_instant_of_a_record_is_named_as_one() -> None:
    """GEN-10: an instant field is a datetime (aware, from the parser), a calendar day a date."""
    for name in instruments.__dict__.values():
        if not dataclasses.is_dataclass(name) or not isinstance(name, type):
            continue
        for field, hint in get_type_hints(name).items():
            if field.endswith("_at"):
                assert datetime in (hint, *getattr(hint, "__args__", ())), field
            if field.endswith("_date"):
                assert hint is date, field


def test_records_survive_a_recording(market: SyntheticMarket, es: dict[str, Any]) -> None:
    records = {
        "equity": parse_security(doc_of(market.equity("SPX"))),
        "equity_option": parse_equity_option(doc_of(market.equity_option("SPX   261218P06000000"))),
        "option_chain": parse_option_chain(doc_of(market.option_chain("AAPL")), "AAPL"),
        "future": parse_future(doc_of(market.future("/ESZ6"))),
        "futures_option_chain": parse_futures_option_chain(es, "/ES"),
    }
    option = records["futures_option_chain"].expirations[0].contract(5, "put")
    assert option is not None
    records["future_option"] = option
    assert set(records) == set(RESULTS)
    for call, record in records.items():
        again = decode_result(call, encode_result(call, record))
        assert again == record and type(again) is type(record), call
