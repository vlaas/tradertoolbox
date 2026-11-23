"""The synthetic stream events and the fake DXLink server's scenarios (plan 3.2 and 3.3)."""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from s3t.broker.fake.market import NY, UNDERLYINGS, SyntheticMarket
from s3t.broker.fake.ticks import (
    RATE,
    REMOVE_EVENT,
    SNAPSHOT_BEGIN,
    SNAPSHOT_END,
    SNAPSHOT_SNIP,
    Ticks,
    market_instant,
    parse_candle_symbol,
    smile_vol,
    svi_params,
)
from s3t.broker.interface import CandlePeriod, EventType, Sub
from s3t.pricing import black_iv, tau_cal

from .conftest import ASOF, StreamRig

pytestmark = pytest.mark.anyio

AT = ASOF + timedelta(minutes=7, seconds=13)


@pytest.fixture(scope="module")
def ticks(market: SyntheticMarket) -> Ticks:
    return Ticks(market)


def chain_symbols(market: SyntheticMarket, underlying: str, index: int) -> list[str]:
    chain = market.option_chain(underlying)
    assert chain is not None
    strikes = chain["items"][0]["expirations"][index]["strikes"]
    return [s[k] for s in strikes for k in ("call-streamer-symbol", "put-streamer-symbol")]


def test_the_same_seed_gives_the_same_events(market: SyntheticMarket) -> None:
    symbols = ["SPY", "SPX", "AAPL", "/ESZ26:XCME", *chain_symbols(market, "SPY", 4)[:40]]
    one, two = Ticks(market), Ticks(SyntheticMarket(ASOF, seed=1))
    other = Ticks(SyntheticMarket(ASOF, seed=2))
    two.events("Quote", symbols, AT + timedelta(hours=2))  # another order of asking
    for event in ("Quote", "Trade", "Summary", "Greeks", "Profile"):
        assert one.events(event, symbols, AT) == two.events(event, symbols, AT)
    assert one.events("Trade", ["SPY"], AT) != other.events("Trade", ["SPY"], AT)
    history = (one.candles("SPY{=15m}", ASOF - timedelta(days=3), AT),)
    assert history[0] == two.candles("SPY{=15m}", ASOF - timedelta(days=3), AT)


def test_unknown_symbols_have_no_events(ticks: Ticks) -> None:
    unknown = ["NOSUCH", ".SPY261120C601.37", ".SPY200101C600", "./XX1Z26C1:XCME", ""]
    for event in ("Quote", "Trade", "Greeks", "Summary", "Profile"):
        assert ticks.events(event, unknown, AT) == []
    assert ticks.candles("NOSUCH{=d}", ASOF - timedelta(days=5), AT) == []
    assert ticks.candles("SPY{=d,price=mark}", ASOF - timedelta(days=5), AT) == []
    assert parse_candle_symbol("SPY{=15m}") == ("SPY", 900)
    assert parse_candle_symbol("SPY{=h,tho=true}") == ("SPY", 3600)
    assert parse_candle_symbol("SPY{=w}") is None and parse_candle_symbol("SPY") is None


def test_option_quotes_lie_on_the_fixed_smile(market: SyntheticMarket, ticks: Ticks) -> None:
    """The mid of each quote inverts back to the SVI slice it was priced from."""
    symbols = chain_symbols(market, "SPX", 12)
    quotes = {q["eventSymbol"]: q for q in ticks.events("Quote", symbols, AT)}
    greeks = {g["eventSymbol"]: g for g in ticks.events("Greeks", symbols, AT)}
    assert set(quotes) == set(greeks) == set(symbols)
    spot = ticks.price("SPX", AT)
    assert spot is not None
    rows = []
    for symbol, quote in quotes.items():
        inst = ticks.instrument(symbol)
        assert inst is not None and inst.expires is not None
        if quote["bidPrice"] == "NaN":
            assert quote["askPrice"] <= 0.1 and quote["bidSize"] == 0
            continue
        assert 0 < quote["bidPrice"] < quote["askPrice"]
        tau = tau_cal(AT, inst.expires)
        rows.append((quote, greeks[symbol], inst, tau))
    assert len(rows) > 100 and len(rows) < len(symbols)  # some far strikes have no bid
    tau = np.array([r[3] for r in rows])
    strike = np.array([r[2].strike for r in rows])
    is_call = np.array([r[2].is_call for r in rows])
    forward = spot * np.exp(RATE * tau)
    mid = np.array([(r[0]["bidPrice"] + r[0]["askPrice"]) / 2 for r in rows])
    solved = black_iv(mid, forward, strike, tau, np.exp(-RATE * tau), is_call, tick=0.05)
    model = smile_vol(rows[0][2].vol, tau, np.log(strike / forward))
    stated = np.array([r[1]["volatility"] for r in rows])
    assert np.allclose(stated, model, atol=1e-6)
    ok = solved.fail == 0
    # Near the money a tick moves the volatility little. Far from it the price is nearly
    # all intrinsic value and the rounding dominates.
    near = ok & (np.abs(np.log(strike / forward)) < 0.06)
    assert near.sum() > 60
    assert np.abs(solved.iv[near] - model[near]).max() < 0.01
    # A skew: the low strikes carry more volatility than those at the money.
    order = np.argsort(strike)
    by_strike = model[order]
    assert by_strike[0] > by_strike[len(order) // 2] > by_strike.min()
    assert strike[order][int(np.argmin(by_strike))] > spot  # the lowest point is above the money
    a, b, rho, m, s = svi_params(0.14, 0.1)
    assert a > 0 and b > 0 and rho < 0 and a + b * s == pytest.approx(0.14**2 * 0.1)
    calls = [r for r in rows if r[2].is_call]
    assert all(0 <= r[1]["delta"] <= 1 for r in calls)
    # Near the money time costs value. (A deep in-the-money European put gains with time.)
    assert all(r[1]["theta"] < 0 for r, close in zip(rows, near, strict=True) if close)


def test_an_option_is_priced_until_it_expires(market: SyntheticMarket, ticks: Ticks) -> None:
    # After the close of its expiration day a 0DTE option still has 15 minutes to live. The
    # raw smile has no value there; the events must come all the same (found 2026-11-23).
    chain = market.option_chain("SPY")
    assert chain is not None
    first = chain["items"][0]["expirations"][0]
    expiry = datetime.fromisoformat(first["expiration-date"]).replace(hour=21, tzinfo=UTC)
    symbols = [s[side] for s in first["strikes"] for side in ("call-streamer-symbol",)]
    for event in ("Quote", "Greeks", "Trade", "Summary"):
        docs = ticks.events(event, symbols, expiry + timedelta(minutes=5))
        assert len(docs) == len(symbols), event
    values = ticks.option_values(symbols, expiry + timedelta(minutes=5))
    assert all(v["volatility"] > 0 and v["price"] >= 0 for v in values.values())


def test_candles_of_all_periods_are_cut_from_one_path(ticks: Ticks) -> None:
    day = datetime(2026, 11, 18, tzinfo=UTC)
    after = day + timedelta(days=1, hours=23)

    def bars(period: str) -> list[dict]:
        found = ticks.candles(f"SPY{{={period}}}", day, after)[:-1]  # without the end marker
        return [b for b in found if day <= datetime.fromtimestamp(b["time"] / 1000, UTC) < after]

    daily = bars("d")
    assert len(daily) == 2
    wednesday = daily[-1]
    for period, count in (("m", 390), ("5m", 78), ("15m", 26), ("h", 7)):
        of_day = [b for b in bars(period) if b["time"] < wednesday["time"] + 86_400_000]
        assert len(of_day) == count
        oldest, newest = of_day[-1], of_day[0]
        assert oldest["open"] == wednesday["open"] and newest["close"] == wednesday["close"]
        assert max(b["high"] for b in of_day) == wednesday["high"]
        assert min(b["low"] for b in of_day) == wednesday["low"]
        assert sum(b["volume"] for b in of_day) == wednesday["volume"]
        assert all(b["low"] <= min(b["open"], b["close"]) for b in of_day)
        assert all(b["high"] >= max(b["open"], b["close"]) for b in of_day)
    # The bar in progress: its close is the price now, and the trade shows the same price.
    live = ticks.candle("SPY{=m}", AT)
    trade = ticks.events("Trade", ["SPY"], AT)[0]
    assert live is not None and live["close"] == trade["price"]
    assert live["time"] == int(AT.timestamp() // 60 * 60 * 1000)


def test_candle_history_newest_first_with_snapshot_flags_and_depth(ticks: Ticks) -> None:
    history = ticks.candles("SPY{=d}", datetime(2026, 1, 1, tzinfo=UTC), AT)
    # The last event is the end marker, not a bar, as live (note B, addendum "WP-0.4").
    *history, marker = history
    assert marker["eventFlags"] == SNAPSHOT_END | REMOVE_EVENT and marker["close"] == "NaN"
    times = [b["time"] for b in history]
    assert times == sorted(times, reverse=True)
    assert history[0]["eventFlags"] == SNAPSHOT_BEGIN
    assert {b["eventFlags"] for b in history[1:]} == {0}
    assert all(datetime.fromtimestamp(t / 1000, UTC).weekday() < 5 for t in times)
    # A history cut at the depth of 8,000 bars ends with the snip marker: a midnight at or
    # before its oldest bar, no prices (the streamer dumps, note B, addendum of 2026-11-24).
    *minutes, snip = ticks.candles("SPY{=m}", datetime(2020, 1, 1, tzinfo=UTC), AT)
    assert len(minutes) == 8000
    assert snip["eventFlags"] == SNAPSHOT_SNIP | REMOVE_EVENT and snip["close"] == "NaN"
    assert snip["time"] % 86_400_000 == 0 and 0 < minutes[-1]["time"] - snip["time"] < 86_400_000
    index = ticks.candles("SPX{=d}", ASOF - timedelta(days=5), AT)
    assert index[0]["volume"] == "NaN" and index[0]["close"] > 0


def test_a_clock_change_moves_the_session_in_utc(ticks: Ticks) -> None:
    """New York left summer time on 1 November 2026. Bars are stamped in UTC."""
    at = datetime(2026, 11, 3, 23, 0, tzinfo=UTC)
    bars = ticks.candles("SPY{=30m}", datetime(2026, 10, 29, tzinfo=UTC), at)[:-1]
    first_of_day: dict[str, datetime] = {}
    for bar in reversed(bars):
        stamp = datetime.fromtimestamp(bar["time"] / 1000, UTC)
        first_of_day.setdefault(stamp.date().isoformat(), stamp)
    opens = {day: (stamp.hour, stamp.minute) for day, stamp in first_of_day.items()}
    assert opens == {
        "2026-10-29": (13, 30),
        "2026-10-30": (13, 30),
        "2026-11-02": (14, 30),
        "2026-11-03": (14, 30),
    }
    assert all(s.astimezone(NY).hour == 9 for s in first_of_day.values())


def test_a_closed_market_stands_still(ticks: Ticks) -> None:
    saturday = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
    close = datetime(2026, 11, 20, 21, 0, tzinfo=UTC)
    assert market_instant(saturday) == close and market_instant(AT) == AT
    assert market_instant(datetime(2026, 11, 23, 12, 0, tzinfo=UTC)) == close  # Monday, early
    symbols = ["SPY", "SPX", ".SPY261127C600"]
    for event in ("Quote", "Trade", "Greeks", "Summary"):
        assert ticks.events(event, symbols, saturday) == ticks.events(
            event, symbols, saturday + timedelta(hours=7)
        )
    summary = ticks.events("Summary", ["SPY"], saturday)[0]
    assert summary["dayClosePrice"] == ticks.events("Trade", ["SPY"], saturday)[0]["price"]


def test_an_index_quote_is_not_a_price(ticks: Ticks) -> None:
    quote = ticks.events("Quote", ["SPX"], AT)[0]
    trade = ticks.events("Trade", ["SPX"], AT)[0]
    assert quote["bidSize"] == "NaN" and quote["askSize"] == "NaN"
    assert quote["askPrice"] - quote["bidPrice"] > 50 and trade["size"] == "NaN"
    assert UNDERLYINGS["SPX"].kind == "index"


async def test_quote_scenarios(rig: StreamRig) -> None:
    rig.dxlink.no_bid = {"SPY"}
    rig.dxlink.no_ask = {"AAPL"}
    rig.dxlink.index_without_quote = True
    session = await rig.open()
    wanted = (EventType.QUOTE, EventType.TRADE)
    await session.subscribe([Sub(s, e) for s in ("SPY", "AAPL", "SPX") for e in wanted])
    await rig.clock.settle()
    quotes = {r.symbol: r for r in rig.rows(EventType.QUOTE)}
    assert set(quotes) == {"SPY", "AAPL"}  # the index sends no quote
    assert math.isnan(quotes["SPY"].bid) and quotes["SPY"].ask > 0
    assert math.isnan(quotes["AAPL"].ask) and quotes["AAPL"].bid > 0
    assert {r.symbol for r in rig.rows(EventType.TRADE)} == {"SPY", "AAPL", "SPX"}


async def test_candle_scenarios(rig: StreamRig) -> None:
    gap = (datetime(2026, 11, 17, tzinfo=UTC), datetime(2026, 11, 19, tzinfo=UTC))
    suspect = datetime(2026, 11, 13, tzinfo=UTC)
    rig.dxlink.candle_gaps["SPY"] = gap
    rig.dxlink.candle_suspects["SPY"] = suspect
    session = await rig.open()
    start = datetime(2026, 11, 9, tzinfo=UTC)
    await session.subscribe([Sub("SPY", EventType.CANDLE, CandlePeriod(1, "day"), start)])
    await rig.clock.settle()
    bars = [b for b in rig.rows(EventType.CANDLE) if not b.removed]
    assert [b.time.day for b in bars] == [20, 19, 16, 13, 12, 11, 10, 9]  # no 17th, no 18th
    odd = next(b for b in bars if b.time == suspect)
    assert odd.high > 5 * odd.close
    assert all(b.high < 1.1 * b.close for b in bars if b is not odd)


async def test_summary_and_profile_come_once(rig: StreamRig) -> None:
    session = await rig.open()
    await session.subscribe([Sub("AAPL", EventType.SUMMARY), Sub("AAPL", EventType.PROFILE)])
    rig.tick_every_second()
    await rig.clock.advance(5)
    assert len(rig.rows(EventType.SUMMARY)) == 1 and len(rig.rows(EventType.PROFILE)) == 1
    profile = rig.rows(EventType.PROFILE)[0]
    assert "synthetic" in profile.description and profile.trading_status == "active"
    assert profile.halt_start is None and profile.low_52w < profile.high_52w
    summary = rig.rows(EventType.SUMMARY)[0]
    assert summary.prev_close > 0 and summary.open_interest == 0.0
