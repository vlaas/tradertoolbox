"""Manager and session together (BRK-02, BRK-03, BRK-06; SUB-03): the two "done when"
sentences of WP-0.4.

- A dropped or silent stream recovers with the full demand restored and the gap reported.
- Demand beyond the budget reduces the lowest class first (also in test_feed.py).

The session reopens empty and reports the gap; the manager sends the granted set again, the
highest class first, and says ``resubscribing`` until it is done."""

from datetime import timedelta

import pytest

from broker.test_stream_recovery import demand, held_at_server
from broker.test_stream_session import option_quotes
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import EventBatch, EventType, Gap, StreamState, Sub
from s3t.config import SubscriptionsConfig
from s3t.subs.feed import FillState, Priority

from .conftest import FeedRig

pytestmark = pytest.mark.anyio


async def test_a_dropped_stream_recovers_the_full_demand_and_reports_the_gap(
    rig: FeedRig, market: SyntheticMarket
) -> None:
    await rig.up()
    subs = demand(market)  # 134 pairs: quotes, trades, a candle, 130 option quotes
    views = subs[:4]
    rig.feed.consumer("scan", Priority.BACKGROUND).set(subs[4:])
    rig.feed.consumer("watchlist", Priority.VIEWS).set(views)
    rig.tick_every_second()
    await rig.clock.advance(3)
    assert rig.dxlink.subscriptions() == held_at_server(subs)
    last_before = rig.batches()[-1]
    first_session = rig.dxlink.session
    messages = len(rig.sent("FEED_SUBSCRIPTION"))

    rig.dxlink.drop()
    await rig.clock.settle()
    assert rig.phases()[-1] == "disconnected"
    status = rig.feed.status()
    assert (status.granted, status.sent, status.resubscribing) == (134, 0, False)
    dropped_at = rig.clock.wall()
    await rig.clock.advance(4)

    # The full demand is back on a new session of the server.
    assert rig.dxlink.session is not first_session
    assert rig.dxlink.subscriptions() == held_at_server(subs)
    status = rig.feed.status()
    assert (status.granted, status.sent, status.pending) == (134, 134, 0)
    assert (status.stream.phase, status.stream.reconnects) == ("connected", 1)
    assert status.stream.subscribed == 134 and not status.resubscribing

    # The gap: from the last batch before the drop to the new session, before any new data.
    (gap,) = rig.gaps()
    assert gap.last_seq == last_before.seq and gap.since == last_before.received_at
    assert dropped_at < gap.until <= dropped_at + timedelta(seconds=2)
    assert status.stream.last_gap == gap and "closed" in gap.reason
    # What a reader saw, in order: the loss, the gap, the new session, the manager's state
    # around the data that came back.
    after = rig.seen[rig.seen.index(last_before) + 1 :]
    kinds = [
        i.phase if isinstance(i, StreamState | FillState) else type(i).__name__
        for i in after
        if not isinstance(i, EventBatch)
    ]
    assert kinds == [
        "disconnected",
        "connecting",
        "Gap",
        "connected",
        "resubscribing",
        "resubscribed",
    ]
    refill, done = rig.fills()[-2:]
    assert (refill.granted, refill.pending) == (134, 134) and (done.granted, done.pending) == (
        134,
        0,
    )
    between = after[after.index(refill) + 1 : after.index(done)]
    assert between and all(isinstance(i, EventBatch) for i in between)
    assert {i.event for i in between if isinstance(i, EventBatch)} == {
        EventType.QUOTE,
        EventType.TRADE,
        EventType.CANDLE,
    }
    # The refill goes the highest class first: the views' four pairs, then the background.
    again = rig.sent("FEED_SUBSCRIPTION")[messages:]
    assert [len(m["add"]) for m in again] == [1, 2, 1, 100, 30]
    assert [a["symbol"] for m in again[:3] for a in m["add"]] == ["SPY", "SPY", "SPX", "SPY{=m}"]
    # The data goes on.
    seen = len(rig.batches(EventType.TRADE))
    await rig.clock.advance(3)
    assert len(rig.batches(EventType.TRADE)) > seen


async def test_a_silent_stream_recovers_the_full_demand(
    market: SyntheticMarket, vclock: VirtualClock
) -> None:
    rig = FeedRig(market, vclock)  # the stall time is the configured 60 s
    await rig.up()
    subs = demand(market)
    rig.feed.consumer("chain", Priority.VIEWS).set(subs)
    rig.tick_every_second()
    await vclock.advance(30)
    assert rig.feed.status().stream.stalls == 0  # data every second: no stall

    rig.dxlink.silent = True
    await vclock.advance(67)
    assert rig.feed.status().stream.stalls == 1 and "stalled" in rig.phases()
    rig.dxlink.silent = False
    await vclock.advance(5)
    # Treated as a drop: a new session, the gap, and the full demand again.
    assert rig.phases()[-3:] == ["disconnected", "connecting", "connected"]
    assert rig.dxlink.subscriptions() == held_at_server(subs)
    (gap,) = rig.gaps()
    assert "no data" in gap.reason
    assert [f.phase for f in rig.fills()][-2:] == ["resubscribing", "resubscribed"]
    seen = len(rig.batches(EventType.TRADE))
    await vclock.advance(3)
    assert len(rig.batches(EventType.TRADE)) > seen
    await rig.aclose()


async def test_demand_changed_while_down_is_what_comes_back(
    rig: FeedRig, market: SyntheticMarket
) -> None:
    await rig.up()
    subs = demand(market)
    chain = rig.feed.consumer("chain", Priority.VIEWS)
    chain.set(subs)
    await rig.clock.advance(2)
    await rig.settled()
    rig.dxlink.refuse_connects = 2
    rig.dxlink.drop()
    await rig.clock.settle()
    extra = Sub("AAPL", EventType.QUOTE)
    chain.set([*subs[3:], extra])
    await rig.clock.advance(10)
    await rig.settled()
    assert rig.dxlink.subscriptions() == held_at_server([*subs[3:], extra])
    # Nothing is removed on a session that never held it.
    assert not any("remove" in m for m in rig.sent("FEED_SUBSCRIPTION"))
    (gap,) = rig.gaps()
    assert isinstance(gap, Gap) and gap.until - gap.since >= timedelta(seconds=1)


async def test_a_drop_in_the_middle_of_a_refill_starts_it_again(
    rig: FeedRig, market: SyntheticMarket
) -> None:
    await rig.up()
    subs = option_quotes(market, 450)
    rig.feed.consumer("chain", Priority.VIEWS).set(subs)
    await rig.clock.advance(1.0)
    assert 0 < len(rig.dxlink.subscriptions()) < 450  # the pace holds the rest back
    rig.dxlink.drop()
    await rig.clock.advance(8)
    await rig.settled()
    assert rig.dxlink.subscriptions() == {("Quote", s.symbol) for s in subs}
    status = rig.feed.status()
    assert (status.sent, status.pending, status.stream.subscribed) == (450, 0, 450)
    # Nothing was sent twice on one connection: the session's count equals the server's.
    assert len(rig.dxlink.subscriptions()) == status.stream.subscribed


async def test_the_budget_holds_through_a_reconnect(
    market: SyntheticMarket, vclock: VirtualClock
) -> None:
    rig = FeedRig(market, vclock, SubscriptionsConfig(limit=12, reserve=2))
    await rig.up()
    subs = option_quotes(market, 16)
    rig.feed.consumer("scan", Priority.BACKGROUND).set(subs[:8])
    rig.feed.consumer("chain", Priority.VIEWS).set(subs[8:])
    await vclock.advance(1)
    await rig.settled()
    held = {("Quote", s.symbol) for s in [*subs[8:], *subs[:2]]}
    assert rig.dxlink.subscriptions() == held
    rig.dxlink.drop()
    await vclock.advance(4)
    await rig.settled()
    # The same grant comes back, the higher class first, and nobody is told twice.
    assert rig.dxlink.subscriptions() == held
    assert [len(m["add"]) for m in rig.sent("FEED_SUBSCRIPTION")[-2:]] == [8, 2]
    assert len(rig.reductions()) == 1 and rig.feed.cap_refusals == 0
    await rig.aclose()
