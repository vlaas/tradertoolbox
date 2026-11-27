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
    dailies = [d for d in got if d <= TODAY + timedelta(days=14)]
    november = [date(2026, 11, n) for n in (20, 23, 24, 25, 27, 30)]  # not Thanksgiving, the 26th
    assert dailies == november + [date(2026, 12, n) for n in (1, 2, 3, 4)]
    weeklies = [d for d in got if TODAY + timedelta(days=14) < d <= TODAY + timedelta(days=63)]
    # Christmas and New Year are Fridays: their weeklies are on the Thursdays before, as live
    # for Christmas (note B, addendum "WP-0.3").
    assert weeklies == [date(2026, 12, n) for n in (11, 18, 24, 31)] + [
        date(2027, 1, n) for n in (8, 15, 22)
    ]
    later = [d for d in got if d > TODAY + timedelta(days=63)]
    # The third Friday of June 2027 is the observed Juneteenth.
    assert [d for d in later if d.weekday() != 4] == [date(2027, 6, 17)]
    assert all(d == third_friday(d.year, d.month) for d in later if d != date(2027, 6, 17))
    assert later[-1] == third_friday(2028, 12)


def test_spx_am_is_third_fridays_only(market: SyntheticMarket) -> None:
    got = dates(market, "SPX", "SPX")
    assert date(2027, 6, 17) in got  # for the third Friday, a holiday
    assert all(d == third_friday(d.year, d.month) for d in got if d != date(2027, 6, 17))
    chain = market.option_chain("SPX")
    assert chain is not None
    am = next(i for i in chain["items"] if i["root-symbol"] == "SPX")
    assert {e["settlement-type"] for e in am["expirations"]} == {"AM"}


def test_stock_has_no_dailies(market: SyntheticMarket) -> None:
    got = dates(market, "AAPL", "AAPL")
    moved = [date(2026, 12, 24), date(2026, 12, 31), date(2027, 6, 17)]  # Fridays were holidays
    assert [d for d in got if d.weekday() != 4] == moved


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


def test_dividends_are_stated_as_the_live_documents_state_them(market: SyntheticMarket) -> None:
    """Note B, addendum "WP-1.1": the last ex-date and its cash, a next date of years ago, no
    annual amount; an index has old dates and zeros and no time of an update. The check of
    2026-11-22 had read SPX's document for SPY's, so it missed `dividend-updated-at`."""
    for symbol, days_ago, cash in (("SPY", 60, "1.75"), ("AAPL", 80, "0.26")):
        doc = market.market_metrics(symbol)
        assert doc is not None
        assert doc["dividend-ex-date"] == (TODAY - timedelta(days=days_ago)).isoformat()
        assert float(doc["dividend-rate-per-share"]) == float(cash)
        assert doc["annual-dividend-per-share"] == "0.0"
        assert doc["dividend-next-date"] < "2023" and "dividend-updated-at" in doc
    spx = market.market_metrics("SPX")
    assert spx is not None and spx["dividend-rate-per-share"] == "0.0"
    assert spx["dividend-ex-date"] == "2020-05-30" and "dividend-updated-at" not in spx


def test_the_markets_own_schedule_repeats_the_last_dividend(market: SyntheticMarket) -> None:
    spy = UNDERLYINGS["SPY"]
    last = TODAY - timedelta(days=60)
    assert market.last_dividend(spy) == (last, 1.75)
    assert market.last_dividend(UNDERLYINGS["SPX"]) is None
    sched = market.dividends(spy, TODAY, TODAY + timedelta(days=200))
    assert [(d.ex_date, d.amount, d.projected) for d in sched] == [
        (last + timedelta(days=91), 1.75, True),
        (last + timedelta(days=182), 1.75, True),
    ]
    assert market.dividends(spy, TODAY, TODAY + timedelta(days=30)) == []
    assert market.dividends(UNDERLYINGS["SPX"], TODAY, TODAY + timedelta(days=400)) == []
    pv = market.pv_dividends("SPY", TODAY, TODAY + timedelta(days=200), 0.04)
    assert 3.4 < pv < 3.5 and market.pv_dividends("NOSUCH", TODAY, TODAY, 0.04) == 0.0


def test_an_option_settles_at_the_close_and_expires_when_the_broker_says(
    market: SyntheticMarket,
) -> None:
    """Decided 2026-11-24: the price runs to the settlement instant; `expires-at` stays 16:15."""
    spy, spx = UNDERLYINGS["SPY"], UNDERLYINGS["SPX"]
    day = dates(market, "SPY", "SPY")[0]
    close = market.session(day)[1]
    assert market.settles_at(spy.roots[0], day) == close
    assert market._expires_at(spy, spy.roots[0], day) == close + timedelta(minutes=15)
    am = next(r for r in spx.roots if r.settlement == "AM" and r.monthlies_only)
    monthly = dates(market, "SPX", "SPX")[0]
    assert market.settles_at(am, monthly) == market.session(monthly)[0]
