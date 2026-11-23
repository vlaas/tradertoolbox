"""The session recovers by itself (BRK-02, BRK-03, BRK-06): after a drop, a refused
connection, a silent stream, a frozen stream and a refused token. Each time the full set of
subscriptions is back and the gap is reported."""

from datetime import UTC, datetime, timedelta

import pytest

from s3t.broker.fake.clock import VirtualClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import (
    BrokerUnavailable,
    EventBatch,
    EventType,
    Gap,
    QuoteToken,
    StreamState,
    Sub,
)
from s3t.broker.tastytrade.dxlink import regular_hours

from .conftest import ASOF, StreamRig
from .test_stream_session import option_quotes

pytestmark = pytest.mark.anyio

SATURDAY = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)


def demand(market: SyntheticMarket) -> list[Sub]:
    return [
        Sub("SPY", EventType.QUOTE),
        Sub("SPY", EventType.TRADE),
        Sub("SPX", EventType.TRADE),
        Sub("SPY{=m}", EventType.CANDLE, from_time=ASOF - timedelta(minutes=5)),
        *option_quotes(market, 130),
    ]


def held_at_server(subs: list[Sub]) -> set[tuple[str, str]]:
    return {(s.event.value, s.symbol) for s in subs}


async def test_a_dropped_stream_recovers_the_full_demand_and_reports_the_gap(
    rig: StreamRig, market: SyntheticMarket
) -> None:
    session = rig.session()
    session.start()
    subs = demand(market)
    session.subscribe(subs)
    rig.tick_every_second()
    await rig.clock.advance(3)
    assert rig.dxlink.subscriptions() == held_at_server(subs)
    last_before = rig.batches()[-1]
    first_session = rig.dxlink.session

    rig.dxlink.drop()
    await rig.clock.settle()
    assert rig.phases()[-1] == "disconnected" and session.status().reason
    dropped_at = rig.clock.wall()
    await rig.clock.advance(4)

    # The full demand is back on a new session of the server.
    assert rig.dxlink.session is not first_session
    assert rig.dxlink.subscriptions() == held_at_server(subs)
    assert rig.phases()[-3:] == ["connecting", "resubscribing", "connected"]

    # The gap: from the last batch before the drop to the new session, before any new data.
    (gap,) = rig.gaps()
    assert gap.last_seq == last_before.seq and gap.since == last_before.received_at
    assert dropped_at < gap.until <= dropped_at + timedelta(seconds=2)
    assert "closed" in gap.reason
    between = [i for i in rig.items if last_before.seq < i.seq < gap.seq]
    assert between and not any(isinstance(i, EventBatch) for i in between)
    after = [i for i in rig.items if i.seq > gap.seq and isinstance(i, EventBatch)]
    assert {b.event for b in after} == {EventType.QUOTE, EventType.TRADE, EventType.CANDLE}

    status = session.status()
    assert (status.phase, status.reconnects, status.stalls) == ("connected", 1, 0)
    assert status.last_gap == gap and status.held == len(subs) and status.pending == 0
    # The data goes on.
    seen = len(rig.batches(EventType.TRADE))
    await rig.clock.advance(3)
    assert len(rig.batches(EventType.TRADE)) > seen


async def test_refused_connections_are_retried_with_growing_waits(rig: StreamRig) -> None:
    rig.dxlink.refuse_connects = 4
    session = rig.session()
    session.subscribe([Sub("SPY", EventType.QUOTE)])
    session.start()
    attempts = []
    for _ in range(16):
        await rig.clock.advance(1)
        attempts.append(rig.dxlink.connect_attempts)
    # Attempts at 0, 1, 3, 7 and 15 seconds: waits of 1, 2, 4 and 8 seconds.
    assert attempts == [2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 5, 5]
    assert rig.phases()[-1] == "connected" and rig.gaps() == []
    assert rig.dxlink.subscriptions() == {("Quote", "SPY")}
    assert session.status().reconnects == 0  # the first session that opened


async def test_a_silent_stream_in_market_hours_is_a_stall(
    rig: StreamRig, market: SyntheticMarket
) -> None:
    assert regular_hours(ASOF)
    session = rig.session(stall_after=20.0)
    session.start()
    subs = demand(market)
    session.subscribe(subs)
    rig.tick_every_second()
    await rig.clock.advance(30)
    assert session.status().stalls == 0  # data every second: no stall

    rig.dxlink.silent = True
    await rig.clock.advance(19)
    assert session.status().phase == "connected"
    await rig.clock.advance(8)
    states = [i for i in rig.items if isinstance(i, StreamState)]
    stalled = next(s for s in states if s.phase == "stalled")
    assert stalled.reason and stalled.reason.startswith("no data for 2")
    assert session.status().stalls == 1
    # It is treated as a drop: a new session with the full demand.
    assert rig.phases()[-4:] == ["disconnected", "connecting", "resubscribing", "connected"]
    assert rig.dxlink.subscriptions() == held_at_server(subs)
    assert len(rig.gaps()) == 1 and "no data" in rig.gaps()[0].reason

    # Still silent: the next stall takes twice as long, so a quiet symbol does not cause a
    # reconnect every few seconds.
    await rig.clock.advance(35)
    assert session.status().stalls == 1
    await rig.clock.advance(15)
    assert session.status().stalls == 2

    # Data again: once it flows, the wait is back to the configured one.
    rig.dxlink.silent = False
    await rig.clock.advance(30)
    assert session.status().stalls == 2 and session.status().phase == "connected"
    rig.dxlink.silent = True
    await rig.clock.advance(27)
    assert session.status().stalls == 3


async def test_silence_on_a_closed_market_is_not_a_stall(market: SyntheticMarket) -> None:
    assert not regular_hours(SATURDAY)
    clock = VirtualClock(SATURDAY)
    rig = StreamRig(market, clock)
    session = rig.session(stall_after=20.0)
    session.start()
    session.subscribe(demand(market))
    rig.tick_every_second()
    await clock.advance(600)
    status = session.status()
    assert (status.phase, status.stalls, status.reconnects) == ("connected", 0, 0)
    # The synthetic market stands still on a Saturday: only the first values arrived.
    assert status.last_data_at is not None and status.last_data_at < SATURDAY + timedelta(seconds=5)
    assert 19 <= len(rig.sent("KEEPALIVE")) <= 20
    await rig.aclose()


async def test_the_stall_clock_starts_when_the_market_opens(market: SyntheticMarket) -> None:
    before_open = datetime(2026, 11, 23, 14, 29, 30, tzinfo=UTC)  # a Monday, 09:29:30
    clock = VirtualClock(before_open)
    rig = StreamRig(market, clock)
    session = rig.session(stall_after=20.0)
    session.start()
    session.subscribe([Sub("SPY", EventType.QUOTE)])
    await clock.advance(45)  # 15 s into the session, 45 s without data: not a stall yet
    assert session.status().stalls == 0
    await clock.advance(15)
    assert session.status().stalls == 1
    await rig.aclose()


async def test_a_frozen_stream_is_dropped_at_any_hour(market: SyntheticMarket) -> None:
    clock = VirtualClock(SATURDAY)
    rig = StreamRig(market, clock)
    session = rig.session()
    session.start()
    subs = [Sub("SPY", EventType.QUOTE), Sub("SPY", EventType.TRADE)]
    session.subscribe(subs)
    await clock.advance(40)
    rig.dxlink.frozen = True  # the last message was the keepalive answer at 30 s
    await clock.advance(45)
    assert session.status().phase == "connected"
    await clock.advance(15)
    assert session.status().phase in ("disconnected", "connecting")
    assert session.status().reason == "no message for 60 s"
    # Still frozen: the new session is not authorized in time, and the attempts go on.
    await clock.advance(60)
    assert "not authorized within 20 s" in {
        i.reason for i in rig.items if isinstance(i, StreamState)
    }
    rig.dxlink.frozen = False
    await clock.advance(40)
    assert session.status().phase == "connected"
    assert rig.dxlink.subscriptions() == held_at_server(subs)
    assert session.status().stalls == 0 and len(rig.gaps()) == 1
    await rig.aclose()


async def test_a_refused_token_is_replaced(rig: StreamRig) -> None:
    session = rig.session()
    session.start()
    session.subscribe([Sub("SPY", EventType.QUOTE)])
    await rig.synced()
    assert len(rig.rest.quote_tokens) == 1

    rig.dxlink.expire_tokens()
    rig.dxlink.drop()
    await rig.clock.advance(5)
    reasons = [i.reason for i in rig.items if isinstance(i, StreamState) and i.reason]
    assert "the quote token was refused" in reasons
    assert len(rig.rest.quote_tokens) == 2
    assert [m["token"] for m in rig.sent("AUTH")] == [
        rig.rest.quote_tokens[0],
        rig.rest.quote_tokens[0],
        rig.rest.quote_tokens[1],
    ]
    assert session.status().phase == "connected"
    assert rig.dxlink.subscriptions() == {("Quote", "SPY")}


async def test_a_broker_that_gives_no_token_is_retried(rig: StreamRig) -> None:
    calls = 0

    async def flaky() -> QuoteToken:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BrokerUnavailable("GET /api-quote-tokens: HTTP 503 on 5 attempts")
        return await rig.broker.quote_token()

    session = rig.session(token_source=flaky)
    session.start()
    await rig.clock.settle()
    state = [i for i in rig.items if isinstance(i, StreamState)][-1]
    assert state.phase == "disconnected" and state.reason
    assert state.reason.startswith("BrokerUnavailable: GET /api-quote-tokens")
    await rig.clock.advance(2)
    assert session.status().phase == "connected" and calls == 2


async def test_demand_changed_while_down_is_what_comes_back(
    rig: StreamRig, market: SyntheticMarket
) -> None:
    session = rig.session()
    session.start()
    subs = demand(market)
    session.subscribe(subs)
    await rig.clock.advance(2)
    await rig.synced()
    rig.dxlink.refuse_connects = 2
    rig.dxlink.drop()
    await rig.clock.settle()
    session.unsubscribe(subs[:3])
    extra = Sub("AAPL", EventType.QUOTE)
    session.subscribe([extra])
    await rig.clock.advance(10)
    await rig.synced()
    assert rig.dxlink.subscriptions() == held_at_server([*subs[3:], extra])
    assert not any("remove" in m for m in rig.sent("FEED_SUBSCRIPTION"))
    (gap,) = rig.gaps()
    assert isinstance(gap, Gap) and gap.until - gap.since >= timedelta(seconds=3)
