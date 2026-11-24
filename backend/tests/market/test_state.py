"""The latest values and the flush (MKT-02, MKT-03, MKT-07).

The market state runs on the real feed, session and conversion against the fake stream, so a
value has gone the whole way from the broker's wire. Where a test needs a certain event it puts
a batch of its own into the feed.
"""

import asyncio
import logging
import math
from datetime import UTC, timedelta

import pytest

from s3t.broker.interface import CandlePeriod, EventBatch, EventType, Quote, Sub, Trade
from s3t.config import SubscriptionsConfig
from s3t.market.state import EVENTS, SWEEP_S, YIELD_EVERY, Flush, MarketState
from s3t.subs.feed import Lost, Priority

from .conftest import ASOF, PERIOD, MarketRig, quotes

pytestmark = pytest.mark.anyio

QUOTE, TRADE = EventType.QUOTE, EventType.TRADE
SPY, SPX, AAPL = Sub("SPY", QUOTE), Sub("SPX", QUOTE), Sub("AAPL", QUOTE)


def bid(rig: MarketRig, symbol: str) -> float:
    return rig.state.column(QUOTE, "bid", [symbol])[0]


# ---------------------------------------------------------------------- the latest value


async def test_the_latest_value_per_symbol_and_event_type(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY, Sub("SPY", TRADE), Sub("SPX", TRADE))
    quote, trade = rig.state.latest(QUOTE, "SPY"), rig.state.latest(TRADE, "SPY")
    assert quote is not None and isinstance(quote.record, Quote)
    assert trade is not None and isinstance(trade.record, Trade)
    assert quote.record == rig.rows(QUOTE, "SPY")[-1]
    # The receipt time is the one of the batch, an aware instant in UTC (GEN-10).
    assert quote.received_at == rig.batches(QUOTE)[-1].received_at
    assert quote.received_at.tzinfo is UTC
    assert rig.state.latest(QUOTE, "AAPL") is None and rig.state.latest(QUOTE, "SPX") is None

    before = quote.record
    await rig.clock.advance(3)
    rig.dxlink.tick()
    await rig.clock.settle()
    after = rig.state.latest(QUOTE, "SPY")
    assert after is not None and after.record != before
    assert after.record == rig.rows(QUOTE, "SPY")[-1]
    assert after.received_at == ASOF + timedelta(seconds=3.06)
    assert quote.record == before  # what a caller was given does not change under it


async def test_an_event_replaces_the_whole_value(rig: MarketRig) -> None:
    """A bid that has gone missing is missing: it does not keep its old value (MKT-07)."""
    await rig.up()
    await rig.want(SPY)
    assert bid(rig, "SPY") > 0
    rig.dxlink.no_bid = {"SPY"}
    await rig.clock.advance(1)
    rig.dxlink.tick()
    await rig.clock.settle()
    assert math.isnan(bid(rig, "SPY"))
    assert rig.state.column(QUOTE, "ask", ["SPY"])[0] > 0


async def test_columns_for_an_array_operation(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY, AAPL)
    column = rig.state.column(QUOTE, "bid", ["AAPL", "NOSUCH", "SPY"])
    assert column[0] == rig.rows(QUOTE, "AAPL")[-1].bid
    assert math.isnan(column[1])  # no value reads as missing, never as zero
    assert column[2] == rig.rows(QUOTE, "SPY")[-1].bid
    received = rig.state.received(QUOTE, ["AAPL", "NOSUCH"])
    assert received[0] is not None and received[1] is None
    assert rig.state.column(QUOTE, "bid", []) == []
    with pytest.raises(ValueError):
        rig.state.column(QUOTE, "no such field", ["SPY"])


async def test_the_last_event_received_wins(rig: MarketRig) -> None:
    """Also when the broker's messages come late and in the wrong order."""
    await rig.up()
    await rig.want(SPY)
    rig.dxlink.hold()
    for _ in range(3):
        await rig.clock.advance(1)
        rig.dxlink.tick()
    rig.dxlink.release(reverse=True)
    await rig.clock.settle()
    latest = rig.state.latest(QUOTE, "SPY")
    assert latest is not None and latest.record == rig.rows(QUOTE, "SPY")[-1]
    # Twice in one batch: the later one.
    twice = [["SPY", "SPY"], [1.0, 2.0], [3.0, 4.0], *[[1.0, 1.0]] * 2, *[[None, None]] * 2]
    await rig.publish(EventBatch(0, QUOTE, rig.clock.wall(), tuple(twice)))
    assert bid(rig, "SPY") == 2.0


async def test_candles_are_not_read(rig: MarketRig) -> None:
    """Their history comes newest first, so the last one received is the oldest bar."""
    assert EventType.CANDLE not in EVENTS and len(EVENTS) == len(EventType) - 1
    await rig.up()
    day = Sub("SPY", EventType.CANDLE, CandlePeriod(1, "day"), ASOF - timedelta(days=10))
    await rig.want(day, SPY)
    assert rig.rows(EventType.CANDLE)  # the feed carried them
    assert rig.state.status().values[QUOTE] == 1
    assert set(rig.state.status().values) == set(EVENTS)


# ---------------------------------------------------------------------- the flush


async def test_a_flush_holds_only_what_changed(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY, SPX, AAPL, Sub("SPY", TRADE))
    flushes = rig.collect(rig.state.changes("test"))
    await rig.clock.advance(2)
    assert flushes == []  # nothing changed, so nothing is flushed
    await rig.publish(quotes(rig, AAPL=(199.0, 199.1)))
    assert flushes == []  # not before the period is over
    await rig.clock.advance(PERIOD)
    assert [f.changed for f in flushes] == [{QUOTE: frozenset({"AAPL"})}]
    # At the end of a period that began when the state was started.
    assert flushes[0].symbols == {"AAPL"} and flushes[0].at == ASOF + timedelta(seconds=2.5)
    await rig.clock.advance(2)
    assert len(flushes) == 1  # what was flushed is not flushed again

    await rig.publish(quotes(rig, SPY=(1.0, 2.0)), quotes(rig, SPX=(1.0, 2.0), SPY=(1.0, 2.1)))
    trade = (["SPY"], [1.5], [1.0], [None], [9.0])
    await rig.publish(EventBatch(0, TRADE, rig.clock.wall(), trade))
    await rig.clock.advance(PERIOD)
    changed = {QUOTE: frozenset({"SPY", "SPX"}), TRADE: frozenset({"SPY"})}
    assert [f.changed for f in flushes[1:]] == [changed]
    assert rig.state.status().flushes == 2


async def test_a_live_tick_flushes_the_symbols_that_moved(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY, SPX, AAPL)
    flushes = rig.collect(rig.state.changes("test"))
    await rig.clock.advance(PERIOD)
    seen = len(rig.batches(QUOTE))
    rig.dxlink.tick()
    await rig.clock.advance(PERIOD)
    moved = {row.symbol for batch in rig.batches(QUOTE)[seen:] for row in batch.rows()}
    assert moved and set().union(*(f.symbols for f in flushes)) == moved


async def test_one_flush_per_period_however_many_events(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY)
    flushes = rig.collect(rig.state.changes("test"))
    assert rig.state.period() == PERIOD  # as the server granted it
    for _ in range(20):  # every 0.1 s for two seconds
        await rig.publish(quotes(rig, SPY=(1.0, 2.0)))
        await rig.clock.advance(0.1)
    assert len(flushes) == 4


async def test_the_configured_period_holds_until_the_server_grants_one(rig: MarketRig) -> None:
    assert rig.state.period() == 2.0 and rig.state.status().period == 2.0
    await rig.up()
    assert rig.state.period() == PERIOD


async def test_a_late_taker_gets_the_union_and_nothing_is_lost(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY, SPX, AAPL)
    quick, late = rig.state.changes("quick"), rig.state.changes("late")
    flushes = rig.collect(quick)
    for symbol in ("SPY", "SPX", "SPY", "AAPL"):
        await rig.publish(quotes(rig, **{symbol: (1.0, 2.0)}))
        await rig.clock.advance(PERIOD)
    assert [f.symbols for f in flushes] == [{"SPY"}, {"SPX"}, {"SPY"}, {"AAPL"}]
    flush = await late.__anext__()  # one flush with all of it: no queue has built up
    assert flush.changed == {QUOTE: frozenset({"SPY", "SPX", "AAPL"})}
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(late.__anext__(), 0.01)


async def test_a_taker_starts_empty_and_ends_when_closed(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY)
    taker = rig.state.changes("test")
    flushes = rig.collect(taker)
    await rig.clock.advance(PERIOD)
    assert flushes == [] and bid(rig, "SPY") > 0  # the values first wanted are in the state
    taker.close()
    taker.close()
    await rig.publish(quotes(rig, SPY=(1.0, 2.0)))
    await rig.clock.advance(PERIOD)
    assert flushes == []
    await rig.state.aclose()
    ended = rig.state.changes("after the end")
    assert [f async for f in ended] == []


# ---------------------------------------------------------------------- pairs not granted


async def test_the_value_of_a_released_pair_is_dropped_and_flushed(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY, AAPL)
    flushes = rig.collect(rig.state.changes("test"))
    await rig.clock.advance(PERIOD)
    await rig.want(SPY)
    await rig.clock.advance(PERIOD)
    assert rig.state.latest(QUOTE, "AAPL") is None and math.isnan(bid(rig, "AAPL"))
    assert rig.state.latest(QUOTE, "SPY") is not None
    assert flushes[-1].changed == {QUOTE: frozenset({"AAPL"})}  # the taker is told

    # An event that was still on its way brings the value back for a moment only.
    await rig.publish(quotes(rig, AAPL=(199.0, 199.1)))
    assert rig.state.latest(QUOTE, "AAPL") is not None
    await rig.clock.advance(SWEEP_S + PERIOD)
    assert rig.state.latest(QUOTE, "AAPL") is None


async def test_a_pair_that_the_budget_cut_does_not_look_live(market, vclock) -> None:
    """SUB-03: its last value would stand still among values that move."""
    config = SubscriptionsConfig(limit=3, reserve=1)
    rig = MarketRig(market, vclock, config)
    try:
        await rig.up()
        await rig.want(SPY, AAPL)
        assert rig.state.latest(QUOTE, "AAPL") is not None
        rig.feed.consumer("positions", Priority.POSITIONS).set([Sub("SPX", TRADE)])
        await rig.settled()
        assert [r.consumer for r in rig.reductions()] == ["test"]
        await rig.clock.advance(PERIOD)
        assert rig.state.latest(QUOTE, "AAPL") is None
        assert rig.state.latest(QUOTE, "SPY") is not None
        assert rig.state.latest(TRADE, "SPX") is not None
    finally:
        await rig.aclose()


async def test_a_pair_wanted_by_another_consumer_stays(rig: MarketRig) -> None:
    await rig.up()
    other = rig.feed.consumer("other", Priority.BACKGROUND)
    other.set([AAPL])
    await rig.want(SPY, AAPL)
    await rig.want(SPY)
    await rig.clock.advance(SWEEP_S + PERIOD)
    assert rig.state.latest(QUOTE, "AAPL") is not None
    other.close()
    await rig.settled()
    await rig.clock.advance(PERIOD)
    assert rig.state.latest(QUOTE, "AAPL") is None


# ---------------------------------------------------------------------- losses


async def test_after_a_drop_the_values_stay_and_are_confirmed_again(rig: MarketRig) -> None:
    """On a closed market: nothing moves, and the refill sends every value as it was. Each is
    live again only when the new session has sent it, and each counts as changed then."""
    await rig.clock.advance(24 * 3600)  # Saturday
    await rig.up()
    await rig.want(SPY, AAPL)
    await rig.clock.advance(1)
    before = {s: rig.state.latest(QUOTE, s) for s in ("SPY", "AAPL")}
    assert all(v is not None and rig.state.is_live(v.received_at) for v in before.values())
    flushes = rig.collect(rig.state.changes("test"))

    rig.dxlink.refuse_connects = 4
    rig.dxlink.drop()
    await rig.clock.advance(0.1)
    assert rig.state.live_since is None
    for symbol, value in before.items():
        assert rig.state.latest(QUOTE, symbol) == value  # still there
        assert value is not None and not rig.state.is_live(value.received_at)
    await rig.clock.advance(1)
    assert flushes == []  # a lost session changes no value
    await rig.clock.advance(30)
    assert rig.feed.status().stream.phase == "connected"
    for symbol, value in before.items():
        after = rig.state.latest(QUOTE, symbol)
        assert after is not None and value is not None
        assert after.record == value.record and after.received_at > value.received_at
        assert rig.state.is_live(after.received_at)
    assert flushes[0].changed == {QUOTE: frozenset({"SPY", "AAPL"})}
    assert len(flushes) == 1


async def test_lost_batches_leave_no_value_confirmed(rig: MarketRig) -> None:
    """Which pairs they held is not known, so every value waits for its next event."""
    await rig.up()
    await rig.want(SPY, AAPL)
    await rig.clock.advance(1)
    aapl = rig.state.latest(QUOTE, "AAPL")
    assert aapl is not None and rig.state.is_live(aapl.received_at)
    rig.state._take(Lost(rig.clock.wall(), 3))
    assert rig.state.status().lost == 3 and not rig.state.is_live(aapl.received_at)
    assert rig.state.latest(QUOTE, "AAPL") == aapl
    await rig.publish(quotes(rig, SPY=(1.0, 2.0)))
    spy = rig.state.latest(QUOTE, "SPY")
    assert spy is not None and rig.state.is_live(spy.received_at)
    assert not rig.state.is_live(aapl.received_at) and not rig.state.is_live(None)


async def test_an_item_that_fails_does_not_end_the_reader(
    rig: MarketRig, caplog: pytest.LogCaptureFixture
) -> None:
    await rig.up()
    await rig.want(SPY)
    await rig.clock.advance(1)
    held = rig.state.latest(QUOTE, "SPY")
    assert held is not None
    broken = EventBatch(0, QUOTE, rig.clock.wall(), (["SPY", "AAPL"], [1.0]))
    with caplog.at_level(logging.ERROR):
        await rig.publish(broken, broken)
    status = rig.state.status()
    assert status.failures == 2 and caplog.text.count("market state: an item") == 1
    assert not rig.state.is_live(held.received_at)  # what the batch held is not known
    await rig.publish(quotes(rig, SPY=(7.0, 8.0)))
    assert bid(rig, "SPY") == 7.0


async def test_a_flush_that_fails_does_not_end_the_flushes(
    rig: MarketRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rig.up()
    await rig.want(SPY)
    flushes = rig.collect(rig.state.changes("test"))
    monkeypatch.setattr(rig.feed, "granted", lambda: 1 / 0)
    await rig.publish(quotes(rig, SPY=(1.0, 2.0)))
    await rig.clock.advance(PERIOD)
    assert flushes == [] and rig.state.failures == 1
    monkeypatch.undo()
    await rig.clock.advance(PERIOD)
    assert [f.symbols for f in flushes] == [{"SPY"}]


async def test_the_end_of_the_feed_ends_the_takers(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY)
    flushes: list[Flush] = rig.collect(rig.state.changes("test"))
    await rig.feed.aclose()
    await rig.clock.settle()
    assert rig.state.live_since is None and flushes == []
    assert all(task.done() for task in rig._takers)


async def test_a_full_queue_does_not_hold_the_event_loop(rig: MarketRig) -> None:
    """The reader's queue gives its items without a pause; the market state makes one."""
    await rig.up()
    await rig.want(SPY)
    for n in range(10 * YIELD_EVERY):
        rig.feed._on_item(quotes(rig, SPY=(float(n), n + 1.0)))
    others = 0
    while bid(rig, "SPY") != 10 * YIELD_EVERY - 1:
        await asyncio.sleep(0)
        others += 1
        assert others < 100
    assert others >= 10


async def test_the_status_counts_what_is_held(rig: MarketRig) -> None:
    await rig.up()
    await rig.want(SPY, AAPL, Sub("SPY", TRADE))
    status = rig.state.status()
    assert status.values[QUOTE] == 2 and status.values[TRADE] == 1
    assert (status.lost, status.failures) == (0, 0) and status.live_since is not None
    assert status.last_batch_at == rig.batches()[-1].received_at
    assert repr(rig.state) == "MarketState(3 values)"


async def test_a_state_that_is_closed_stays_closed(rig: MarketRig) -> None:
    state = MarketState(rig.feed, now=rig.broker.data_time, period=1.0, sleep=rig.clock.sleep)
    await state.aclose()
    state.start()
    assert state._tasks == []
