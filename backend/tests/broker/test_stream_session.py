"""The DXLink session against the fake stream: handshake, decoding into the application's
records, subscription calls and the hard cap (BRK-02). Recovery is in
test_stream_recovery.py; pacing and the content of the subscriptions belong to the feed
(tests/subs)."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import (
    BrokerError,
    Candle,
    CandlePeriod,
    EventType,
    Quote,
    QuoteToken,
    StreamRefused,
    StreamState,
    Sub,
    Trade,
)
from s3t.broker.tastytrade.convert import EVENT_FIELDS

from .conftest import ASOF, StreamRig

pytestmark = pytest.mark.anyio

SPY_QUOTE = Sub("SPY", EventType.QUOTE)
SPY_TRADE = Sub("SPY", EventType.TRADE)
DAY = CandlePeriod(1, "day")


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
    session = await rig.open()
    assert rig.phases() == ["connecting", "connected"]
    # All six channels are opened and set up before the session says it is connected.
    kinds = [m["type"] for m in rig.dxlink.received]
    assert kinds == ["SETUP", "AUTH"] + ["CHANNEL_REQUEST"] * 6 + ["FEED_SETUP"] * 6
    assert rig.sent("AUTH")[0]["token"] == rig.rest.quote_tokens[0]
    setups = rig.sent("FEED_SETUP")
    assert {m["acceptDataFormat"] for m in setups} == {"COMPACT"}
    assert setups[0]["acceptEventFields"] == {"Quote": list(EVENT_FIELDS[EventType.QUOTE])}
    assert setups[0]["acceptAggregationPeriod"] == 0.5
    assert rig.dxlink.subscriptions() == set() and session.status().subscribed == 0

    await session.subscribe([SPY_QUOTE, SPY_TRADE])
    await rig.clock.settle()
    assert rig.dxlink.subscriptions() == {("Quote", "SPY"), ("Trade", "SPY")}

    quote, trade = rig.batches(EventType.QUOTE)[0], rig.batches(EventType.TRADE)[0]
    assert quote.count == 1 and len(quote.columns) == 7
    assert quote.received_at == ASOF and quote.received_at.tzinfo is UTC
    row = next(quote.rows())
    assert isinstance(row, Quote) and row.symbol == "SPY" and 0 < row.bid < row.ask
    assert isinstance(row.bid_size, float) and row.bid_size > 0
    assert row.bid_time is None  # the fake sends 0, dxFeed's "no time"
    assert quote.column("bid") == [row.bid]
    sold = next(trade.rows())
    assert isinstance(sold, Trade) and sold.price > 0 and sold.time == ASOF
    seqs = [item.seq for item in rig.items]
    assert seqs == list(range(1, len(seqs) + 1))
    status = session.status()
    assert (status.phase, status.subscribed, status.candles) == ("connected", 2, 0)
    assert (status.limit, status.candle_limit) == (11_000, 100)
    assert status.aggregation_period == 0.5 and status.last_data_at == ASOF


async def test_the_servers_field_order_is_used(rig: StreamRig) -> None:
    rig.dxlink.reverse_fields = True
    session = await rig.open()
    await session.subscribe([SPY_QUOTE, SPY_TRADE])
    await rig.clock.settle()
    quote = rig.rows(EventType.QUOTE, "SPY")[0]
    assert 0 < quote.bid < quote.ask and quote.bid_size > 0
    assert rig.rows(EventType.TRADE, "SPY")[0].time == ASOF
    assert session.parse_errors == 0


async def test_text_and_missing_numbers_become_floats(rig: StreamRig) -> None:
    rig.dxlink.no_bid = {"SPY"}
    session = await rig.open()
    await session.subscribe([SPY_QUOTE, Sub("SPX", EventType.QUOTE), Sub("SPX", EventType.TRADE)])
    await rig.clock.settle()
    spy, spx = rig.rows(EventType.QUOTE, "SPY")[0], rig.rows(EventType.QUOTE, "SPX")[0]
    # A missing bid is a price of 0 without a size on the wire, and NaN here (MKT-07).
    assert math.isnan(spy.bid) and spy.ask > 0 and math.isnan(spy.bid_size)
    # An index quote has its sizes as the text "NaN" on the wire.
    assert math.isnan(spx.bid_size) and math.isnan(spx.ask_size) and spx.bid > 0
    trade = rig.rows(EventType.TRADE, "SPX")[0]
    assert math.isnan(trade.size) and math.isnan(trade.day_volume) and trade.price > 0
    for batch in rig.batches():
        for name, column in zip(("bid", "ask"), batch.columns[1:3], strict=True):
            assert all(isinstance(x, float) for x in column), name


async def test_data_follows_the_clock(rig: StreamRig) -> None:
    session = await rig.open()
    await session.subscribe([SPY_QUOTE, SPY_TRADE])
    rig.tick_every_second()
    await rig.clock.advance(5)
    trades = rig.rows(EventType.TRADE, "SPY")
    assert len(trades) >= 4
    assert [t.time for t in trades] == sorted(t.time for t in trades)
    assert len({t.price for t in trades}) > 1
    assert session.status().last_data_at == rig.clock.wall()


async def test_unsubscribe(rig: StreamRig) -> None:
    session = await rig.open()
    await session.subscribe([SPY_QUOTE, SPY_TRADE])
    await session.unsubscribe([SPY_TRADE])
    await rig.clock.settle()
    assert rig.dxlink.subscriptions() == {("Quote", "SPY")}
    assert rig.sent("FEED_SUBSCRIPTION")[-1]["remove"] == [{"type": "Trade", "symbol": "SPY"}]
    rig.tick_every_second()
    before = len(rig.batches(EventType.TRADE))
    await rig.clock.advance(3)
    assert len(rig.batches(EventType.TRADE)) == before
    assert len(rig.batches(EventType.QUOTE)) > 1
    assert session.status().subscribed == 1


async def test_candles(rig: StreamRig) -> None:
    session = await rig.open()
    start = ASOF - timedelta(days=10)
    await session.subscribe([Sub("SPY", EventType.CANDLE, DAY, start)])
    await rig.clock.settle()
    # The broker module builds dxFeed's candle symbol from the symbol and the period.
    add = rig.sent("FEED_SUBSCRIPTION")[0]["add"][0]
    assert add == {
        "type": "Candle",
        "symbol": "SPY{=d}",
        "fromTime": round(start.timestamp() * 1000),
    }
    bars = [b for b in rig.batches(EventType.CANDLE)[0].rows() if isinstance(b, Candle)]
    assert all(b.symbol == "SPY" and b.period == DAY for b in bars)
    times = [b.time for b in bars]
    # Newest first: the eight trading days from 11 to 20 November, then the end marker.
    assert times == sorted(times, reverse=True) and len(times) == 8 + 1
    assert times[-1] == start and session.ignored_events == 0
    assert [(b.snapshot_end, b.removed) for b in bars] == [(False, False)] * 8 + [(True, True)]
    assert all(b.low <= b.open <= b.high and b.volume > 0 for b in bars[:8])
    assert session.status().candles == 1
    with pytest.raises(ValueError, match="candle"):
        Sub("SPY", EventType.QUOTE, from_time=start)
    with pytest.raises(ValueError, match="period"):
        Sub("SPY", EventType.CANDLE)


async def test_a_call_is_refused_while_not_connected(rig: StreamRig) -> None:
    session = rig.session()
    with pytest.raises(StreamRefused) as refused:
        await session.subscribe([SPY_QUOTE])
    assert refused.value.reason == "not_connected"
    session.start()
    await rig.until_connected(session)
    await session.subscribe([SPY_QUOTE])
    rig.dxlink.refuse_connects = 100
    rig.dxlink.drop()
    await rig.clock.settle()
    assert session.status().phase == "disconnected"
    for call in (session.subscribe, session.unsubscribe):
        with pytest.raises(StreamRefused) as refused:
            await call([SPY_TRADE])
        assert refused.value.reason == "not_connected"
    # Refused, not queued: nothing but the first pair was ever sent.
    assert len(rig.sent("FEED_SUBSCRIPTION")) == 1


async def test_the_hard_cap(rig: StreamRig, market: SyntheticMarket) -> None:
    session = await rig.open(limit=6, candle_limit=1)
    pairs = option_quotes(market, 8)
    await session.subscribe(pairs[:4])
    with pytest.raises(StreamRefused) as refused:
        await session.subscribe(pairs[4:7])
    assert refused.value.reason == "limit"
    # The whole call is refused: nothing of it was sent.
    assert len(rig.dxlink.subscriptions()) == 4 and session.status().subscribed == 4
    await session.unsubscribe(pairs[:2])
    await session.subscribe(pairs[4:6])
    assert len(rig.dxlink.subscriptions()) == 4

    await session.subscribe([Sub("SPY", EventType.CANDLE, DAY, ASOF)])
    with pytest.raises(StreamRefused) as refused:
        await session.subscribe([Sub("AAPL", EventType.CANDLE, DAY, ASOF)])
    assert refused.value.reason == "candle_limit"
    assert (session.status().subscribed, session.status().candles) == (5, 1)

    # The count belongs to the connection: a new one starts at nothing.
    rig.dxlink.drop()
    await rig.clock.advance(3)
    assert session.status().phase == "connected"
    assert (session.status().subscribed, session.status().candles) == (0, 0)
    await session.subscribe(pairs[:6])
    assert len(rig.dxlink.subscriptions()) == 6


async def test_a_call_goes_at_once_in_messages_of_100(
    rig: StreamRig, market: SyntheticMarket
) -> None:
    session = await rig.open()
    subs = option_quotes(market, 250)
    await session.subscribe(subs)
    # No pacing in the session: that is the manager's (SUB-02).
    assert [len(m["add"]) for m in rig.sent("FEED_SUBSCRIPTION")] == [100, 100, 50]
    assert rig.dxlink.subscriptions() == {("Quote", s.symbol) for s in subs}
    await session.unsubscribe(subs)
    assert [len(m.get("remove", [])) for m in rig.sent("FEED_SUBSCRIPTION")[3:]] == [100, 100, 50]
    assert rig.dxlink.subscriptions() == set() and session.status().subscribed == 0


async def test_a_rate_error_is_passed_on_with_its_event_type(
    rig: StreamRig, market: SyntheticMarket
) -> None:
    rig.dxlink.change_limit = (150, 60.0)
    session = await rig.open()
    subs = option_quotes(market, 200)
    await session.subscribe(subs[:100])
    await session.subscribe(subs[100:])
    await rig.clock.settle()
    (notice,) = rig.notices()
    assert notice.kind == "rate" and "rate is too high" in notice.message
    assert notice.event is EventType.QUOTE
    # The session sends nothing again by itself: the server holds the first hundred.
    assert len(rig.dxlink.subscriptions()) == 100 and len(rig.sent("FEED_SUBSCRIPTION")) == 2
    assert session.status().reconnects == 0


async def test_a_refusal_of_the_server_is_reported(rig: StreamRig, market: SyntheticMarket) -> None:
    rig.dxlink.sub_cap = 5
    session = await rig.open()
    await session.subscribe(option_quotes(market, 8))
    await rig.clock.settle()
    notice = rig.notices()[0]
    assert notice.kind == "refused" and "limit" in notice.message.lower()
    assert notice.event is EventType.QUOTE
    assert rig.dxlink.subscriptions() == set()


async def test_delayed_and_out_of_order_messages_pass_through(rig: StreamRig) -> None:
    session = await rig.open()
    await session.subscribe([SPY_TRADE])
    rig.tick_every_second()
    await rig.clock.advance(1)
    rig.dxlink.hold()
    await rig.clock.advance(3)
    held_back = len(rig.batches(EventType.TRADE))
    rig.dxlink.release(reverse=True)
    await rig.clock.settle()
    late = rig.batches(EventType.TRADE)[held_back:]
    times = [b.column("time")[0] for b in late]
    assert len(late) == 3 and times == sorted(times, reverse=True)
    assert [b.seq for b in late] == sorted(b.seq for b in late)
    assert {b.received_at for b in late} == {rig.clock.wall()}


async def test_a_failing_sink_does_not_stop_the_stream(rig: StreamRig) -> None:
    def sink(item: object) -> None:
        rig.items.append(item)  # type: ignore[arg-type]
        raise RuntimeError("sink failed")

    session = await rig.open(sink=sink)
    await session.subscribe([SPY_QUOTE])
    rig.tick_every_second()
    await rig.clock.advance(3)
    assert len(rig.batches(EventType.QUOTE)) >= 3
    assert session.sink_errors == len(rig.items)


async def test_close(rig: StreamRig) -> None:
    session = await rig.open()
    await session.subscribe([SPY_QUOTE])
    await session.aclose()
    assert rig.phases()[-1] == "closed" and session.status().phase == "closed"
    assert rig.dxlink.session is None  # the server saw the connection close
    with pytest.raises(StreamRefused):
        await session.subscribe([SPY_TRADE])
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
    await rig.until_connected(stream)
    await stream.subscribe([SPY_QUOTE])
    await clock.settle()
    assert broker.dxlink.subscriptions() == {("Quote", "SPY")}
    assert broker.source().kind == "fake" and broker.source().mark == "SYNTHETIC DATA"
    assert broker.data_time() == clock.wall() and broker.market.asof == ASOF
    # The session's hard cap is the limit of the configuration, not the manager's budget.
    assert (stream.status().limit, stream.status().candle_limit) == (11_000, 100)
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
