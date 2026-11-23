"""The DXLink session against the fake stream: handshake, decoding, subscription changes
and their pacing (BRK-02, SUB-02). Recovery is in test_stream_recovery.py."""

from datetime import UTC, datetime, timedelta

import pytest

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import BrokerError, EventType, QuoteToken, StreamState, Sub
from s3t.broker.limits import ChangePacer
from s3t.broker.tastytrade.dxlink import EVENT_FIELDS

from .conftest import ASOF, StreamRig

pytestmark = pytest.mark.anyio

SPY_QUOTE = Sub("SPY", EventType.QUOTE)
SPY_TRADE = Sub("SPY", EventType.TRADE)


def option_quotes(market: SyntheticMarket, n: int) -> list[Sub]:
    chain = market.option_chain("SPY")
    assert chain is not None
    symbols = [
        strike[side]
        for expiration in chain["items"][0]["expirations"]
        for strike in expiration["strikes"]
        for side in ("call-streamer-symbol", "put-streamer-symbol")
    ]
    assert len(symbols) >= n
    return [Sub(symbol, EventType.QUOTE) for symbol in symbols[:n]]


async def test_handshake_then_data(rig: StreamRig) -> None:
    session = rig.session()
    session.start()
    await rig.clock.settle()
    assert rig.phases() == ["connecting", "connected"]
    assert [m["type"] for m in rig.dxlink.received] == ["SETUP", "AUTH"]
    assert rig.sent("AUTH")[0]["token"] == rig.rest.quote_tokens[0]

    session.subscribe([SPY_QUOTE, SPY_TRADE])
    await rig.synced()
    assert rig.dxlink.subscriptions() == {("Quote", "SPY"), ("Trade", "SPY")}
    setups = rig.sent("FEED_SETUP")
    assert {m["acceptDataFormat"] for m in setups} == {"COMPACT"}
    assert setups[0]["acceptEventFields"] == {"Quote": list(EVENT_FIELDS[EventType.QUOTE])}
    assert setups[0]["acceptAggregationPeriod"] == 0.5

    quote, trade = rig.batches(EventType.QUOTE)[0], rig.batches(EventType.TRADE)[0]
    assert quote.fields == EVENT_FIELDS[EventType.QUOTE] and quote.count == 1
    assert quote.received_at == ASOF and quote.received_at.tzinfo is UTC
    row = next(quote.rows())
    assert row["eventSymbol"] == "SPY" and 0 < row["bidPrice"] < row["askPrice"]
    assert next(trade.rows())["price"] > 0
    seqs = [item.seq for item in rig.items]
    assert seqs == list(range(1, len(seqs) + 1))
    status = session.status()
    assert (status.phase, status.held, status.pending) == ("connected", 2, 0)
    assert status.aggregation_period == 0.5 and status.last_data_at == ASOF


async def test_the_servers_field_order_is_used(rig: StreamRig) -> None:
    rig.dxlink.reverse_fields = True
    session = rig.session()
    session.start()
    session.subscribe([SPY_QUOTE])
    await rig.synced()
    batch = rig.batches(EventType.QUOTE)[0]
    assert batch.fields == tuple(reversed(EVENT_FIELDS[EventType.QUOTE]))
    row = next(batch.rows())
    assert row["eventSymbol"] == "SPY" and row["bidPrice"] < row["askPrice"]


async def test_data_follows_the_clock(rig: StreamRig) -> None:
    session = rig.session()
    session.start()
    session.subscribe([SPY_QUOTE, SPY_TRADE])
    rig.tick_every_second()
    await rig.clock.advance(5)
    trades = rig.rows(EventType.TRADE, "SPY")
    assert len(trades) >= 4
    assert [t["time"] for t in trades] == sorted(t["time"] for t in trades)
    assert len({t["price"] for t in trades}) > 1
    assert session.status().last_data_at == rig.clock.wall()


async def test_unsubscribe(rig: StreamRig) -> None:
    session = rig.session()
    session.start()
    session.subscribe([SPY_QUOTE, SPY_TRADE])
    await rig.synced()
    session.unsubscribe([SPY_TRADE])
    session.unsubscribe([Sub("AAPL", EventType.QUOTE)])  # never held: nothing happens
    await rig.synced()
    assert rig.dxlink.subscriptions() == {("Quote", "SPY")}
    assert rig.sent("FEED_SUBSCRIPTION")[-1]["remove"] == [{"type": "Trade", "symbol": "SPY"}]
    rig.tick_every_second()
    before = len(rig.batches(EventType.TRADE))
    await rig.clock.advance(3)
    assert len(rig.batches(EventType.TRADE)) == before
    assert len(rig.batches(EventType.QUOTE)) > 1
    assert session.status().held == 1


async def test_candles(rig: StreamRig) -> None:
    session = rig.session()
    session.start()
    start = ASOF - timedelta(days=10)
    session.subscribe([Sub("SPY{=d}", EventType.CANDLE, from_time=start)])
    await rig.synced()
    add = rig.sent("FEED_SUBSCRIPTION")[0]["add"][0]
    assert add == {
        "type": "Candle",
        "symbol": "SPY{=d}",
        "fromTime": round(start.timestamp() * 1000),
    }
    batch = rig.batches(EventType.CANDLE)[0]
    assert batch.fields == EVENT_FIELDS[EventType.CANDLE]
    times = [row["time"] for row in batch.rows()]
    # Newest first: the eight trading days from 11 to 20 November, then the end marker.
    assert times == sorted(times, reverse=True) and len(times) == 8 + 1
    with pytest.raises(ValueError, match="candle"):
        session.subscribe([Sub("SPY", EventType.QUOTE, from_time=start)])


async def test_changes_go_in_chunks_at_the_pace(rig: StreamRig, market: SyntheticMarket) -> None:
    session = rig.session()
    session.start()
    subs = option_quotes(market, 450)
    session.subscribe(subs)
    await rig.clock.settle()
    # The burst of 100 goes at once; the rest follows at 150 a second.
    assert [len(m["add"]) for m in rig.sent("FEED_SUBSCRIPTION")] == [100]
    assert session.status().pending == 350
    await rig.clock.advance(1.0)
    assert len(rig.dxlink.subscriptions()) == 200
    await rig.clock.advance(2.0)
    await rig.synced()
    assert [len(m["add"]) for m in rig.sent("FEED_SUBSCRIPTION")] == [100, 100, 100, 100, 50]
    assert rig.dxlink.subscriptions() == {("Quote", s.symbol) for s in subs}
    assert session.status().changes_last_minute == 450


async def test_the_minute_cap_holds_changes_back(rig: StreamRig, market: SyntheticMarket) -> None:
    clock = rig.clock
    pacer = ChangePacer(window_cap=300, clock=clock.monotonic, sleep=clock.sleep)
    session = rig.session(pacer=pacer)
    session.start()
    session.subscribe(option_quotes(market, 450))
    await clock.advance(30)
    assert len(rig.dxlink.subscriptions()) == 300 and session.status().pending == 150
    assert session.status().change_limit == 300
    await clock.advance(31)
    await rig.synced()
    assert len(rig.dxlink.subscriptions()) == 450


async def test_a_rate_error_slows_down_and_sends_again(
    rig: StreamRig, market: SyntheticMarket
) -> None:
    rig.dxlink.change_limit = (150, 60.0)
    session = rig.session()
    session.start()
    subs = option_quotes(market, 300)
    session.subscribe(subs)
    rig.tick_every_second()
    await rig.clock.advance(2)
    # The second chunk went over the server's limit. It was refused, not lost.
    assert len(rig.dxlink.subscriptions()) == 100
    assert rig.notices()[0].kind == "rate" and "rate is too high" in rig.notices()[0].message
    status = session.status()
    # What went out just before the error is sent again too: the server confirms nothing.
    assert status.rate_errors >= 1 and status.pending == 300
    await rig.clock.advance(300)
    await rig.synced()
    assert rig.dxlink.subscriptions() == {("Quote", s.symbol) for s in subs}
    assert rig.dxlink.rejected_changes > 0 and session.status().reconnects == 0


async def test_a_pair_wanted_and_dropped_before_its_turn_is_never_sent(
    rig: StreamRig, market: SyntheticMarket
) -> None:
    session = rig.session()
    session.start()
    subs = option_quotes(market, 150)
    session.subscribe(subs)
    session.unsubscribe(subs[100:])
    await rig.clock.advance(2)
    await rig.synced()
    sent = [a["symbol"] for m in rig.sent("FEED_SUBSCRIPTION") for a in m.get("add", [])]
    assert sent == [s.symbol for s in subs[:100]]
    assert not any("remove" in m for m in rig.sent("FEED_SUBSCRIPTION"))


async def test_a_refused_subscription_is_reported(rig: StreamRig, market: SyntheticMarket) -> None:
    rig.dxlink.sub_cap = 5
    session = rig.session()
    session.start()
    session.subscribe(option_quotes(market, 8))
    await rig.synced()
    notice = rig.notices()[0]
    assert notice.kind == "refused" and "limit" in notice.message.lower()
    assert rig.dxlink.subscriptions() == set()


async def test_delayed_and_out_of_order_messages_pass_through(rig: StreamRig) -> None:
    session = rig.session()
    session.start()
    session.subscribe([SPY_TRADE])
    await rig.synced()
    rig.tick_every_second()
    await rig.clock.advance(1)
    rig.dxlink.hold()
    await rig.clock.advance(3)
    held_back = len(rig.batches(EventType.TRADE))
    rig.dxlink.release(reverse=True)
    await rig.clock.settle()
    late = rig.batches(EventType.TRADE)[held_back:]
    times = [next(b.rows())["time"] for b in late]
    assert len(late) == 3 and times == sorted(times, reverse=True)
    assert [b.seq for b in late] == sorted(b.seq for b in late)
    assert {b.received_at for b in late} == {rig.clock.wall()}


async def test_a_failing_sink_does_not_stop_the_stream(rig: StreamRig) -> None:
    def sink(item: object) -> None:
        rig.items.append(item)  # type: ignore[arg-type]
        raise RuntimeError("sink failed")

    session = rig.session(sink=sink)
    session.start()
    session.subscribe([SPY_QUOTE])
    rig.tick_every_second()
    await rig.clock.advance(3)
    assert len(rig.batches(EventType.QUOTE)) >= 3
    assert session.sink_errors == len(rig.items)


async def test_close(rig: StreamRig) -> None:
    session = rig.session()
    session.start()
    session.subscribe([SPY_QUOTE])
    await rig.synced()
    await session.aclose()
    assert rig.phases()[-1] == "closed" and session.status().phase == "closed"
    assert rig.dxlink.session is None  # the server saw the connection close
    session.start()  # a closed session stays closed
    await rig.clock.advance(5)
    assert rig.phases()[-1] == "closed"

    waiting = rig.session()
    rig.dxlink.refuse_connects = 100
    waiting.start()
    await rig.clock.advance(3)
    await waiting.aclose()  # in the middle of a reconnect wait
    last = [i for i in rig.items if isinstance(i, StreamState)][-1]
    assert last.phase == "closed"
    assert rig.clock.sleepers == 0


async def test_the_broker_has_one_stream(rig: StreamRig) -> None:
    from s3t.broker.fake.broker import FakeBroker

    clock = rig.clock
    broker = FakeBroker(clock=clock.monotonic, sleep=clock.sleep, now=clock.wall)
    items: list[object] = []
    stream = broker.stream(items.append)
    stream.start()
    stream.subscribe([SPY_QUOTE])
    await stream.synced()
    await clock.settle()
    assert broker.dxlink.subscriptions() == {("Quote", "SPY")}
    assert broker.source().kind == "fake" and broker.source().mark == "SYNTHETIC DATA"
    assert broker.now() == clock.wall() and broker.market.asof == ASOF
    with pytest.raises(BrokerError, match="one streaming session"):
        broker.stream(items.append)
    await broker.aclose()
    assert stream.status().phase == "closed"


async def test_a_token_near_its_end_is_renewed_at_the_next_connection(rig: StreamRig) -> None:
    issued: list[QuoteToken] = []

    async def short_lived() -> QuoteToken:
        token = await rig.broker.quote_token()
        short = QuoteToken(
            token.url,
            token.token,
            token.level,
            token.issued_at,
            token.issued_at + timedelta(minutes=6),
        )
        issued.append(short)
        return short

    session = rig.session(token_source=short_lived)
    session.start()
    await rig.clock.advance(30)
    rig.dxlink.drop()
    await rig.clock.advance(3)
    assert len(issued) == 1 and rig.phases()[-1] == "connected"  # 5.5 minutes left: kept
    await rig.clock.advance(60)
    rig.dxlink.drop()
    await rig.clock.advance(3)
    assert len(issued) == 2 and rig.phases()[-1] == "connected"
    assert rig.sent("AUTH")[-1]["token"] == rig.rest.quote_tokens[1]
    assert datetime.now(UTC) > ASOF  # the test ran on the virtual clock, not this one
