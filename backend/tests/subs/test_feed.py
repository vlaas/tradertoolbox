"""The in-process feed on the session and the fake stream: demand, the grant, pacing, the
classes, the readers and the use report (SUB-01 to SUB-03, SUB-07). Recovery is in
test_feed_recovery.py."""

import asyncio
from collections.abc import Collection

import pytest

from broker.test_stream_session import option_quotes
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import CandlePeriod, EventBatch, EventType, StreamState, Sub
from s3t.config import SubscriptionsConfig
from s3t.subs.feed import Feed, FeedItem, Lost, Priority, Reader

from .conftest import ASOF, FeedRig

pytestmark = pytest.mark.anyio

SPY_QUOTE = Sub("SPY", EventType.QUOTE)
SPY_TRADE = Sub("SPY", EventType.TRADE)
DAY = CandlePeriod(1, "day")


def wire(subs: list[Sub]) -> set[tuple[str, str]]:
    return {("Quote", s.symbol) for s in subs}


async def test_demand_is_granted_sent_and_delivered(rig: FeedRig) -> None:
    feed: Feed = rig.feed
    views = feed.consumer("watchlist", Priority.VIEWS)
    assert (views.name, views.priority) == ("watchlist", Priority.VIEWS)
    views.set([SPY_QUOTE, SPY_TRADE])  # demand may be stated before the feed is started
    rig.dxlink.refuse_connects = 1  # the session opens a moment later, on its second attempt
    feed.start()
    await rig.clock.advance(1.5)
    await rig.settled()
    assert rig.dxlink.subscriptions() == {("Quote", "SPY"), ("Trade", "SPY")}
    assert rig.rows(EventType.QUOTE, "SPY") and rig.rows(EventType.TRADE, "SPY")
    # The stream's items pass through with the stream's own numbers, in order.
    seqs = [i.seq for i in rig.items]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert [f.phase for f in rig.fills()] == ["resubscribing", "resubscribed"]

    status = feed.status()
    assert (status.granted, status.sent, status.pending) == (2, 2, 0)
    assert (status.budget, status.candle_budget, status.change_budget) == (10_000, 95, 8_000)
    assert status.changes_last_minute == 2 and status.rate_errors == 0 and status.lost == 0
    assert status.stream.phase == "connected" and status.stream.subscribed == 2
    assert not status.resubscribing
    (use,) = status.consumers
    assert (use.name, use.priority, use.wanted, use.granted) == ("watchlist", Priority.VIEWS, 2, 2)
    (by_class,) = status.classes
    assert (by_class.priority, by_class.counted) == (Priority.VIEWS, 2)

    # The full wanted set replaces the one before: the difference is sent.
    views.set([SPY_TRADE, Sub("AAPL", EventType.QUOTE)])
    await rig.clock.advance(0.1)
    await rig.settled()
    assert rig.dxlink.subscriptions() == {("Trade", "SPY"), ("Quote", "AAPL")}
    assert len(rig.fills()) == 2  # a changed demand is not a resubscription
    with pytest.raises(ValueError, match="registered already"):
        feed.consumer("watchlist", Priority.BACKGROUND)
    views.close()
    await rig.clock.advance(0.1)
    await rig.settled()
    assert rig.dxlink.subscriptions() == set() and feed.status().consumers == ()
    feed.consumer("watchlist", Priority.BACKGROUND)  # the name is free again
    with pytest.raises(RuntimeError, match="closed"):
        views.set([SPY_QUOTE])


async def test_changes_go_in_chunks_at_the_pace(rig: FeedRig, market: SyntheticMarket) -> None:
    await rig.up()
    subs = option_quotes(market, 450)
    rig.feed.consumer("chain", Priority.VIEWS).set(subs)
    await rig.clock.advance(0.05)  # the changes are gathered for a moment, then sent
    # The burst of 100 goes at once; the rest follows at 150 a second.
    assert [len(m) for m in rig.adds()] == [100]
    assert rig.feed.status().pending == 350
    await rig.clock.advance(1.0)
    assert len(rig.dxlink.subscriptions()) == 200
    await rig.clock.advance(2.0)
    await rig.settled()
    assert [len(m) for m in rig.adds()] == [100, 100, 100, 100, 50]
    assert rig.dxlink.subscriptions() == wire(subs)
    assert rig.feed.status().changes_last_minute == 450


async def test_the_minute_cap_holds_changes_back(
    market: SyntheticMarket, vclock: VirtualClock
) -> None:
    config = SubscriptionsConfig(changes_per_minute=2300, change_reserve=2000)
    rig = FeedRig(market, vclock, config)
    await rig.up()
    rig.feed.consumer("chain", Priority.VIEWS).set(option_quotes(market, 450))
    await vclock.advance(30)
    status = rig.feed.status()
    assert len(rig.dxlink.subscriptions()) == 300 and status.pending == 150
    assert (status.change_budget, status.changes_last_minute) == (300, 300)
    await vclock.advance(31)
    await rig.settled()
    assert len(rig.dxlink.subscriptions()) == 450
    await rig.aclose()


async def test_a_rate_error_slows_down_and_sends_again(
    rig: FeedRig, market: SyntheticMarket
) -> None:
    rig.dxlink.change_limit = (150, 60.0)
    await rig.up()
    subs = option_quotes(market, 300)
    rig.feed.consumer("chain", Priority.VIEWS).set(subs)
    rig.tick_every_second()
    await rig.clock.advance(2)
    # The second chunk went over the server's limit. It was refused, not lost.
    assert len(rig.dxlink.subscriptions()) == 100
    notice = rig.notices()[0]
    assert notice.kind == "rate" and notice.event is EventType.QUOTE
    status = rig.feed.status()
    # What went out just before the error is sent again too: the server confirms nothing.
    assert status.rate_errors >= 1 and status.pending == 300 and status.sent == 0
    await rig.clock.advance(300)
    await rig.settled()
    assert rig.dxlink.subscriptions() == wire(subs)
    assert rig.dxlink.rejected_changes > 0 and rig.feed.status().stream.reconnects == 0


async def test_a_pair_wanted_and_dropped_before_its_turn_is_never_sent(
    rig: FeedRig, market: SyntheticMarket
) -> None:
    await rig.up()
    subs = option_quotes(market, 250)
    chain = rig.feed.consumer("chain", Priority.VIEWS)
    for n in (250, 180, 220, 150):  # a view that scrolls: one grant for all of it
        chain.set(subs[:n])
    await rig.clock.advance(0.3)
    chain.set(subs[:120])  # while the second chunk still waits for its turn
    await rig.clock.advance(3)
    await rig.settled()
    sent = [symbol for message in rig.adds() for symbol in message]
    assert sent == [s.symbol for s in subs[:120]]
    assert not any("remove" in m for m in rig.sent("FEED_SUBSCRIPTION"))


async def test_removals_go_first_and_the_highest_class_first(
    rig: FeedRig, market: SyntheticMarket
) -> None:
    await rig.up()
    subs = option_quotes(market, 12)
    scan = rig.feed.consumer("scan", Priority.BACKGROUND)  # registered first, lowest class
    chain = rig.feed.consumer("chain", Priority.VIEWS)
    recorder = rig.feed.consumer("recorder", Priority.RECORDER)
    scan.set(subs[0:4])
    chain.set([*subs[4:8], Sub(subs[4].symbol, EventType.GREEKS)])
    recorder.set(subs[8:12])
    await rig.clock.advance(1)
    await rig.settled()
    symbols = [s.symbol for s in subs]
    # One message per class and event type: recorder, views (quotes, then Greeks), background.
    assert rig.adds() == [symbols[8:12], symbols[4:8], symbols[4:5], symbols[0:4]]

    before = len(rig.sent("FEED_SUBSCRIPTION"))
    chain.set(subs[6:8])
    scan.set([*subs[0:4], Sub("AAPL", EventType.QUOTE)])
    await rig.clock.advance(1)
    await rig.settled()
    kinds = [next(k for k in ("add", "remove") if k in m) for m in rig.sent("FEED_SUBSCRIPTION")]
    assert kinds[before:] == ["remove", "remove", "add"]  # quotes and Greeks removed first
    assert len(rig.dxlink.subscriptions()) == 4 + 2 + 4 + 1


async def test_a_pair_wanted_twice_is_sent_once_and_kept_while_one_wants_it(rig: FeedRig) -> None:
    await rig.up()
    chain = rig.feed.consumer("chain", Priority.VIEWS)
    scan = rig.feed.consumer("scan", Priority.BACKGROUND)
    chain.set([SPY_QUOTE, SPY_TRADE])
    scan.set([SPY_QUOTE])
    await rig.clock.advance(1)
    await rig.settled()
    assert rig.adds() == [["SPY"], ["SPY"]]  # one quote, one trade
    status = rig.feed.status()
    assert (status.granted, status.sent) == (2, 2)
    assert [(c.priority, c.wanted, c.granted, c.counted) for c in status.classes] == [
        (Priority.VIEWS, 2, 2, 2),
        (Priority.BACKGROUND, 1, 1, 0),
    ]
    chain.close()
    await rig.clock.advance(1)
    await rig.settled()
    assert rig.dxlink.subscriptions() == {("Quote", "SPY")}
    assert [c.counted for c in rig.feed.status().classes] == [1]


async def test_demand_beyond_the_budget_reduces_the_lowest_class_first(
    market: SyntheticMarket, vclock: VirtualClock
) -> None:
    config = SubscriptionsConfig(limit=12, reserve=2, candle_limit=3, candle_reserve=1)
    rig = FeedRig(market, vclock, config)
    await rig.up()
    subs = option_quotes(market, 20)
    scan = rig.feed.consumer("scan", Priority.BACKGROUND)
    chain = rig.feed.consumer("chain", Priority.VIEWS)
    positions = rig.feed.consumer("positions", Priority.POSITIONS)
    scan.set(subs[0:6])
    chain.set(subs[6:12])
    await vclock.advance(1)
    await rig.settled()
    # Twelve wanted, ten granted (the limit of 12 less the reserve of 2): the background,
    # the lowest class, is cut from the end of its order and told.
    assert rig.dxlink.subscriptions() == wire(subs[6:12] + subs[0:4])
    (told,) = rig.reductions()
    assert (told.consumer, told.priority) == ("scan", Priority.BACKGROUND)
    assert (told.wanted, told.granted, told.cut) == (6, 4, tuple(subs[4:6]))

    # A higher class asks for more: the lower ones give way, the lowest first.
    positions.set(subs[12:18])
    await vclock.advance(1)
    await rig.settled()
    assert rig.dxlink.subscriptions() == wire(subs[12:18] + subs[6:10])
    latest = {r.consumer: r for r in rig.reductions()}
    assert latest["scan"].granted == 0 and latest["scan"].cut == tuple(subs[0:6])
    assert latest["chain"].granted == 4 and latest["chain"].cut == tuple(subs[10:12])
    status = rig.feed.status()
    assert (status.granted, status.budget, status.sent) == (10, 10, 10)
    assert {u.name: (u.wanted, u.granted) for u in status.consumers} == {
        "scan": (6, 0),
        "chain": (6, 4),
        "positions": (6, 6),
    }
    assert [(c.priority, c.counted) for c in status.classes] == [
        (Priority.POSITIONS, 6),
        (Priority.VIEWS, 4),
        (Priority.BACKGROUND, 0),
    ]
    # The session's hard cap was never reached.
    assert status.stream.limit == 12 and status.stream.subscribed == 10
    assert rig.feed.cap_refusals == 0

    # Room again: everyone gets everything, and the end of each reduction is told.
    count = len(rig.reductions())
    positions.set(subs[12:14])
    chain.set(subs[6:8])
    await vclock.advance(1)
    await rig.settled()
    over = rig.reductions()[count:]
    assert {(r.consumer, r.cut) for r in over} == {("scan", ()), ("chain", ())}
    assert len(rig.dxlink.subscriptions()) == 2 + 2 + 6

    # Candles have their own budget: 3 less a reserve of 1.
    bars = [Sub(s, EventType.CANDLE, DAY, ASOF) for s in ("SPY", "AAPL", "SPX")]
    chart = rig.feed.consumer("chart", Priority.VIEWS)
    chart.set(bars)
    await vclock.advance(1)
    await rig.settled()
    latest = {r.consumer: r.cut for r in rig.reductions()}
    # The third candle is over the candle budget. The two granted ones belong to a view, so
    # the background gives way for them.
    assert latest["chart"] == (bars[2],) and latest["scan"] == tuple(subs[4:6])
    assert (rig.feed.status().candles, rig.feed.status().candle_budget) == (2, 2)
    assert rig.feed.status().stream.candles == 2
    await rig.aclose()


async def test_a_refusal_for_the_hard_cap_is_counted_and_not_repeated(
    market: SyntheticMarket, vclock: VirtualClock
) -> None:
    # A fault on purpose: a session with a cap of 5 under a manager that grants 10.
    rig = FeedRig(market, vclock, SubscriptionsConfig(limit=5, reserve=0))
    rig.feed._limits = type(rig.feed._limits)(total=10, candles=5)
    await rig.up()
    subs = option_quotes(market, 8)
    chain = rig.feed.consumer("chain", Priority.VIEWS)
    chain.set(subs)
    await vclock.advance(5)
    assert rig.feed.cap_refusals == 1 and rig.dxlink.subscriptions() == set()
    assert len(rig.sent("FEED_SUBSCRIPTION")) == 0  # refused whole, and not tried again
    chain.set(subs[:5])
    await vclock.advance(1)
    await rig.settled()
    assert len(rig.dxlink.subscriptions()) == 5 and rig.feed.cap_refusals == 1
    await rig.aclose()


async def test_the_worker_survives_an_unexpected_error(rig: FeedRig) -> None:
    await rig.up()
    stream = rig.feed._stream
    real = stream.subscribe
    calls = 0

    async def faulty(subs: Collection[Sub]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("a fault in the session")
        await real(subs)

    stream.subscribe = faulty  # type: ignore[method-assign]
    rig.feed.consumer("watchlist", Priority.VIEWS).set([SPY_QUOTE])
    await rig.clock.advance(1)
    # Logged and counted, not sent, and not settled: nobody is told that all is well.
    assert rig.feed.failures == 1 and rig.dxlink.subscriptions() == set()
    assert rig.feed.status().pending == 1
    await rig.clock.advance(5)
    await rig.settled()
    assert rig.dxlink.subscriptions() == {("Quote", "SPY")} and rig.feed.failures == 1


async def test_readers(rig: FeedRig) -> None:
    feed = rig.feed
    quotes_only = feed.reader("quotes", events=[EventType.QUOTE])
    slow = feed.reader("slow", maxsize=3)
    await rig.up()
    feed.consumer("watchlist", Priority.VIEWS).set([SPY_QUOTE, SPY_TRADE])
    rig.tick_every_second()
    await rig.clock.advance(10)

    async def take(reader: Reader, n: int) -> list[FeedItem]:
        """The next items of a reader; fails instead of waiting for ever."""
        got: list[FeedItem] = []

        async def read() -> None:
            async for item in reader:
                got.append(item)
                if len(got) == n:
                    break

        await asyncio.wait_for(read(), 2)
        return got

    # A reader gets the batches of the event types it named, and every other kind of item.
    got = await take(quotes_only, 8)
    assert {i.event for i in got if isinstance(i, EventBatch)} == {EventType.QUOTE}
    assert [i.phase for i in got if isinstance(i, StreamState)] == ["connecting", "connected"]

    # A full queue loses the newest batches and says so; states are never lost.
    batches_seen = len(rig.batches())
    assert feed.status().lost == batches_seen - 3 > 5
    got = await take(slow, 5)
    assert [type(i) for i in got] == [StreamState] * 2 + [EventBatch] * 3
    numbers = [i.seq for i in got if isinstance(i, StreamState | EventBatch)]
    assert numbers == [1, 2, 3, 4, 5]  # the oldest are kept, the newest lost
    await rig.clock.advance(1)  # there is room again: the loss is told before the next batch
    told, batch = await take(slow, 2)
    assert isinstance(told, Lost) and told.batches == batches_seen - 3
    assert isinstance(batch, EventBatch) and told.at <= rig.clock.wall()

    # A closed reader ends its iteration and gets nothing more.
    slow.close()
    rest = await take(slow, 100)
    assert len(rest) <= 3
    lost = feed.status().lost
    await rig.clock.advance(5)
    assert feed.status().lost == lost
    waiting = asyncio.ensure_future(take(feed.reader("late"), 1))
    await rig.clock.settle()
    assert not waiting.done()
    await rig.feed.aclose()  # closing the feed closes the session and ends every reader
    (last,) = await asyncio.wait_for(waiting, 1)
    assert isinstance(last, StreamState) and last.phase == "closed"
    assert await take(quotes_only, 10_000) is not None  # ends, instead of waiting for ever
