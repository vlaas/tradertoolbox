"""The synthetic market (implementation plan 3.2): deterministic, with the expiration rules
and symbol formats it states."""

import json
from datetime import date, timedelta

from s3t.broker.fake.market import (
    UNDERLYINGS,
    SyntheticMarket,
    occ_symbol,
    parse_occ,
    streamer_symbol,
    strike_text,
    third_friday,
)

from .conftest import ASOF

TODAY = date(2026, 11, 20)  # ASOF in New York


def dates(market: SyntheticMarket, underlying: str, root: str) -> list[date]:
    chain = market.option_chain(underlying)
    assert chain is not None
    item = next(i for i in chain["items"] if i["root-symbol"] == root)
    return [date.fromisoformat(e["expiration-date"]) for e in item["expirations"]]


def test_same_inputs_same_documents(market: SyntheticMarket) -> None:
    again, other_seed = SyntheticMarket(ASOF, 1), SyntheticMarket(ASOF, 2)
    for symbol in UNDERLYINGS:
        assert json.dumps(again.option_chain(symbol)) == json.dumps(market.option_chain(symbol))
        assert again.market_metrics(symbol) == market.market_metrics(symbol)
    assert again.futures_option_chain("ES") == market.futures_option_chain("/ES")
    # The seed varies the noise of the metrics, not the instruments.
    assert other_seed.option_chain("AAPL") == market.option_chain("AAPL")
    assert other_seed.market_metrics("SPY") != market.market_metrics("SPY")


def test_spy_expiration_rules(market: SyntheticMarket) -> None:
    got = dates(market, "SPY", "SPY")
    assert all(d.weekday() < 5 for d in got)
    dailies = [TODAY + timedelta(days=i) for i in range(15)]
    assert [d for d in got if d <= TODAY + timedelta(days=14)] == [
        d for d in dailies if d.weekday() < 5
    ]
    fridays = [d for d in got if TODAY + timedelta(days=14) < d <= TODAY + timedelta(days=63)]
    assert all(d.weekday() == 4 for d in fridays) and len(fridays) == 7
    later = [d for d in got if d > TODAY + timedelta(days=63)]
    assert all(d == third_friday(d.year, d.month) for d in later)
    assert later[-1] == third_friday(2028, 12)


def test_spx_am_is_third_fridays_only(market: SyntheticMarket) -> None:
    got = dates(market, "SPX", "SPX")
    assert all(d == third_friday(d.year, d.month) for d in got)
    chain = market.option_chain("SPX")
    assert chain is not None
    am = next(i for i in chain["items"] if i["root-symbol"] == "SPX")
    assert {e["settlement-type"] for e in am["expirations"]} == {"AM"}


def test_stock_has_no_dailies(market: SyntheticMarket) -> None:
    got = dates(market, "AAPL", "AAPL")
    assert all(d.weekday() == 4 for d in got)


def test_symbol_formats() -> None:
    exp = date(2026, 11, 20)
    assert occ_symbol("SPY", exp, "C", 588.5) == "SPY   261120C00588500"
    assert occ_symbol("SPXW", exp, "P", 6000) == "SPXW  261120P06000000"
    assert streamer_symbol("SPY", exp, "C", 588.5) == ".SPY261120C588.5"
    assert streamer_symbol("SPXW", exp, "P", 6000.0) == ".SPXW261120P6000"
    assert strike_text(12.25) == "12.25"
    assert parse_occ("SPY   261120C00588500") == ("SPY", exp, "C", 588.5)
    assert parse_occ("SPY 261120C588") is None


def test_every_listed_contract_resolves(market: SyntheticMarket) -> None:
    for symbol in UNDERLYINGS:
        chain = market.option_chain(symbol)
        assert chain is not None
        for item in chain["items"]:
            for expiration in item["expirations"][::7]:
                for strike in expiration["strikes"][::40]:
                    doc = market.equity_option(strike["call"])
                    assert doc is not None, strike["call"]
                    assert doc["streamer-symbol"] == strike["call-streamer-symbol"]
                    assert doc["settlement-type"] == expiration["settlement-type"]
    assert market.equity_option("SPY   261120C00588700") is None  # no such strike
    assert market.equity_option("SPY   261121C00600000") is None  # a Saturday


def test_futures(market: SyntheticMarket) -> None:
    chain = market.futures_option_chain("/ES")
    assert chain is not None
    for future in chain["futures"]:
        doc = market.future(future["symbol"])
        assert doc is not None
    esz6 = market.future("/ESZ6")
    assert esz6 is not None and esz6["streamer-symbol"] == "/ESZ26:XCME"
    expirations = chain["option-chains"][0]["expirations"]
    assert {e["settlement-type"] for e in expirations} == {"AM", "PM"}
    for expiration in expirations[::3]:
        strike = expiration["strikes"][5]
        doc = market.future_option(strike["put"])
        assert doc is not None, strike["put"]
        assert doc["streamer-symbol"] == strike["put-streamer-symbol"]
        assert doc["underlying-symbol"] == expiration["underlying-symbol"]
    assert market.future_option("./ESZ6 EW3X6 261120C1") is None


def test_metric_units(market: SyntheticMarket) -> None:
    spy = market.market_metrics("SPY")
    assert spy is not None
    assert 0 < float(spy["implied-volatility-index"]) < 1  # a fraction
    assert float(spy["implied-volatility-30-day"]) > 1  # percent
    assert 0 <= float(spy["implied-volatility-index-rank"]) <= 1
    aapl = market.market_metrics("AAPL")
    assert aapl is not None and aapl["earnings"]["expected-report-date"] == "2026-11-29"
    assert market.market_metrics("NOSUCH") is None


def test_shapes_follow_the_live_check(market: SyntheticMarket) -> None:
    """What the live check of 2026-11-22 showed (note B, addendum of that date)."""
    spx, spy = market.equity("SPX"), market.equity("SPY")
    assert spx is not None and "tick-sizes" not in spx
    assert spy is not None and "tick-sizes" in spy
    chain = market.futures_option_chain("ES")
    assert chain is not None
    assert all("streamer-symbol" in f for f in chain["futures"])
    assert all("cash-settled" in e for e in chain["option-chains"][0]["expirations"])
    future = market.future(chain["futures"][0]["symbol"])
    assert future is not None and "roll-target-symbol" not in future
    assert future["expires-at"].endswith(".000+00:00")
    metrics = market.market_metrics("AAPL")
    assert metrics is not None and "dividend-updated-at" not in metrics
