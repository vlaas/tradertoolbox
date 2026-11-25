"""The ``quotes`` topic (API-03, API-05; MKT-03, MKT-07, SUB-03), and a client that comes back: the
whole way from the broker's wire to the message."""

import asyncio
import math
from pathlib import Path

import pytest

from s3t.api.topics import LONGEST_SYMBOL, MOST_SYMBOLS, RETRY_S
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.interface import AuthError, BrokerProtocolError, EventType, Sub
from s3t.instants import to_micros
from s3t.subs.feed import Priority
from s3t.wire.v1.channel_pb2 import (
    NOTE_KIND_CUT,
    NOTE_KIND_UNKNOWN,
    NOTE_KIND_UNREACHABLE,
    REFUSAL_KIND_FAILED,
    REFUSAL_KIND_INVALID,
    REFUSAL_KIND_UNAVAILABLE,
)
from s3t.wire.v1.market_pb2 import QuoteRows

from .conftest import ApiRig, Socket, make_config, running

pytestmark = pytest.mark.anyio

QUOTE = EventType.QUOTE
COLUMNS = [f.name for f in QuoteRows.DESCRIPTOR.fields if f.name != "symbol"]


def rows(socket: Socket, number: int) -> list[QuoteRows]:
    return [m.quotes.rows for m in socket.of(number) if m.quotes.HasField("rows")]


def latest(socket: Socket, number: int) -> dict[str, dict[str, float]]:
    """What a page holds after every message so far."""
    held: dict[str, dict[str, float]] = {}
    for message in socket.of(number):
        if message.snapshot:
            held = {}
        table = message.quotes.rows
        assert {len(getattr(table, name)) for name in COLUMNS} == {len(table.symbol)}
        for i, symbol in enumerate(table.symbol):
            held[symbol] = {name: getattr(table, name)[i] for name in COLUMNS}
    return held


def notes(socket: Socket, number: int) -> list[list[tuple[str, int]]]:
    """The notes of every message that has some."""
    found = [m.quotes.notes for m in socket.of(number) if m.quotes.HasField("notes")]
    return [[(n.symbol, n.kind) for n in each.note] for each in found]


async def test_a_snapshot_then_the_rows_that_change(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["SPX", "SPY", "AAPL"]})
    await rig.clock.settle()
    first = socket.of(number)[0]
    assert first.snapshot and first.quotes.HasField("notes") and not first.quotes.notes.note
    assert list(first.quotes.rows.symbol) == ["SPX", "SPY", "AAPL"]  # in the client's order
    assert all(math.isnan(x) for name in COLUMNS for x in getattr(first.quotes.rows, name))
    await rig.pass_time(3)
    held = latest(socket, number)
    market = rig.backend.market
    for symbol in ("SPX", "SPY", "AAPL"):
        assert held[symbol]["bid"] == market.column(QUOTE, "bid", [symbol])[0] > 0
        assert held[symbol]["last"] == market.column(EventType.TRADE, "price", [symbol])[0] > 0
        received = market.received(QUOTE, [symbol])[0]
        assert received is not None and held[symbol]["quote_received_us"] == to_micros(received)
    assert math.isnan(held["SPX"]["bid_size"]) and held["SPY"]["bid_size"] > 0
    assert not any(m.snapshot for m in socket.of(number)[1:])


async def test_a_change_holds_only_the_rows_that_changed(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["SPY", "AAPL"]})
    await rig.pass_time(3)
    rig.fake.dxlink.silent = True
    await rig.pass_time(1)
    before = len(socket.of(number))
    rig.fake.dxlink.silent = False
    rig.fake.dxlink.no_bid = {"AAPL"}
    session = rig.fake.dxlink.session
    assert session is not None
    session.push("Quote", rig.fake.dxlink.events("Quote", ["AAPL"], rig.clock.wall()))
    rig.fake.dxlink.silent = True
    await rig.pass_time(1)
    (message,) = socket.of(number)[before:]
    assert list(message.quotes.rows.symbol) == ["AAPL"]
    assert math.isnan(message.quotes.rows.bid[0])  # missing, never zero (MKT-07)
    assert message.quotes.rows.ask[0] > 0 and not message.quotes.HasField("notes")
    await rig.pass_time(5)
    assert len(socket.of(number)) == before + 1  # nothing more


async def test_a_symbol_that_the_broker_does_not_know(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["NOSUCH", "SPY", "/ESZ6", "a b"]})
    await rig.pass_time(4)
    unknown = [("NOSUCH", NOTE_KIND_UNKNOWN), ("/ESZ6", NOTE_KIND_UNKNOWN)]
    assert notes(socket, number)[0] == []
    assert notes(socket, number)[-1] == [*unknown, ("a b", NOTE_KIND_UNKNOWN)]
    held = latest(socket, number)
    assert held["SPY"]["bid"] > 0 and math.isnan(held["NOSUCH"]["bid"])
    text = socket.of(number)[-1].quotes
    assert "/instruments" not in str(text)


async def test_a_broker_that_cannot_be_asked_is_asked_again(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    rig.fake.rest.disconnect(5)  # as many as the client tries
    number = socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(40)
    assert notes(socket, number)[1] == [("SPY", NOTE_KIND_UNREACHABLE)]
    await rig.pass_time(RETRY_S + 5)
    assert notes(socket, number)[-1] == []
    assert latest(socket, number)["SPY"]["bid"] > 0


@pytest.mark.parametrize("error", [AuthError("refused"), BrokerProtocolError("unreadable")])
async def test_an_answer_that_will_not_change_is_not_asked_for_again(
    rig: ApiRig, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    calls: list[str] = []

    async def equity(symbol: str) -> None:
        calls.append(symbol)
        raise error

    monkeypatch.setattr(rig.backend.broker, "equity", equity)
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["SPY", "SPX"]})
    await rig.pass_time(2 * RETRY_S)
    assert calls == ["SPY", "SPX"]
    assert [kind for _, kind in notes(socket, number)[-1]] == [NOTE_KIND_UNREACHABLE] * 2


async def test_one_instrument_is_read_at_a_time(
    rig: ApiRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request limit is shared with the token that a reconnect needs (BRK-02)."""
    under_way, most = 0, 0
    equity = rig.backend.broker.equity

    async def counted(symbol: str) -> object:
        nonlocal under_way, most
        under_way += 1
        most = max(most, under_way)
        await asyncio.sleep(0)
        under_way -= 1
        return await equity(symbol)

    monkeypatch.setattr(rig.backend.broker, "equity", counted)
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(quotes={"symbol": ["SPY", "SPX", "AAPL", "NOSUCH"]})
    await rig.pass_time(5)
    assert most == 1


async def test_the_budget_cuts_from_the_end_and_says_so(
    tmp_path: Path, clock: VirtualClock
) -> None:
    """SUB-03: the consumer is told, and through it the page."""
    config = make_config(tmp_path, 'source = "fake"\n[subscriptions]\nlimit = 7\nreserve = 1\n')
    async with running(config, clock) as rig:
        await rig.up()
        await rig.log_in()
        socket = await rig.socket()
        number = socket.subscribe(quotes={"symbol": ["SPY", "SPX", "AAPL"]})
        await rig.pass_time(4)
        assert notes(socket, number)[-1] == [("AAPL", NOTE_KIND_CUT)]
        held = latest(socket, number)
        assert held["SPY"]["bid"] > 0 and held["SPX"]["last"] > 0
        assert math.isnan(held["AAPL"]["bid"])
        # A higher class takes more, and gives it back.
        position = rig.backend.feed.consumer("position", Priority.POSITIONS)
        position.set([Sub(f".SPY261127C{n}", QUOTE) for n in (600, 601, 602)])
        await rig.pass_time(2)
        cut = [("SPX", NOTE_KIND_CUT), ("AAPL", NOTE_KIND_CUT)]
        assert notes(socket, number)[-1] == cut
        assert math.isnan(latest(socket, number)["SPX"]["last"])  # and not its old value
        position.close()
        await rig.pass_time(2)
        assert notes(socket, number)[-1] == [("AAPL", NOTE_KIND_CUT)]
        assert latest(socket, number)["SPX"]["last"] > 0
        # What is cut from another view is its own matter.
        before = len(notes(socket, number))
        other = socket.subscribe(quotes={"symbol": ["AAPL", "SPX"]})
        await rig.pass_time(2)
        assert notes(socket, other)[-1] == [("AAPL", NOTE_KIND_CUT)]
        rig.backend.feed.consumer("background", Priority.BACKGROUND).set([Sub("SPX", QUOTE)])
        socket.unsubscribe(other)
        await rig.pass_time(2)
        assert len(notes(socket, number)) == before


async def test_a_cut_is_noted_in_the_view_it_cuts(tmp_path: Path, clock: VirtualClock) -> None:
    config = make_config(tmp_path, 'source = "fake"\n[subscriptions]\nlimit = 7\nreserve = 1\n')
    async with running(config, clock) as rig:
        await rig.up()
        await rig.log_in()
        socket = await rig.socket()
        first = socket.subscribe(quotes={"symbol": ["SPY", "SPX"]})  # the whole budget
        await rig.pass_time(4)
        second = socket.subscribe(quotes={"symbol": ["SPY", "AAPL"]})
        await rig.pass_time(4)
        assert notes(socket, second)[-1] == [("AAPL", NOTE_KIND_CUT)]
        assert notes(socket, first) == [[]]


async def test_a_second_view_has_its_values_at_once(rig: ApiRig) -> None:
    await rig.log_in()
    one, two = await rig.socket(), await rig.socket()
    one.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(3)
    number = two.subscribe(quotes={"symbol": ["SPY"]})
    await rig.clock.settle()
    assert two.of(number)[0].quotes.rows.bid[0] > 0  # in the snapshot itself
    assert rig.backend.feed.status().granted == 3  # and it costs nothing


async def test_a_value_that_is_there_does_not_wait_for_the_next_one(rig: ApiRig) -> None:
    """Something else holds the pair, the instrument was not read yet, and the market is quiet."""
    rig.backend.feed.consumer("position", Priority.POSITIONS).set([Sub("SPY", QUOTE)])
    await rig.pass_time(3)
    rig.fake.dxlink.silent = True
    await rig.pass_time(1)
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(2)
    assert latest(socket, number)["SPY"]["bid"] > 0


async def test_nothing_is_asked_for_after_the_view_is_closed(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(quotes={"symbol": ["SPY"]})
    other = socket.subscribe(quotes={"symbol": ["SPX"]})
    await rig.pass_time(3)
    assert len(rig.fake.dxlink.subscriptions()) == 6
    socket.unsubscribe(1)
    await rig.pass_time(1)
    assert {symbol for _, symbol in rig.fake.dxlink.subscriptions()} == {"SPX"}
    before = len(socket.of(1))
    await rig.pass_time(3)
    assert len(socket.of(1)) == before and len(socket.of(other)) > 3


async def test_a_changed_list_keeps_what_both_want(rig: ApiRig) -> None:
    """The new one first, then the end of the old one: nothing is taken from the broker and asked
    of it again."""
    await rig.log_in()
    socket = await rig.socket()
    old = socket.subscribe(quotes={"symbol": ["SPY", "SPX"]})
    await rig.pass_time(3)
    sent = len(rig.fake.dxlink.received)
    new = socket.subscribe(quotes={"symbol": ["SPX", "AAPL"]})
    socket.unsubscribe(old)
    await rig.pass_time(3)
    changes = [m for m in rig.fake.dxlink.received[sent:] if m["type"] == "FEED_SUBSCRIPTION"]
    touched = {e["symbol"] for m in changes for kind in ("add", "remove") for e in m.get(kind, ())}
    assert touched == {"SPY", "AAPL"}
    assert socket.of(new)[0].quotes.rows.last[0] > 0


@pytest.mark.parametrize(
    "symbols",
    [["S"] * (MOST_SYMBOLS + 1) + [f"S{n}" for n in range(MOST_SYMBOLS)], [""], ["SPY", ""]]
    + [["x" * (LONGEST_SYMBOL + 1)]],
)
async def test_what_cannot_be_a_list_of_symbols(rig: ApiRig, symbols: list[str]) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": symbols})
    await rig.clock.settle()
    (message,) = socket.of(number)
    assert message.refused.kind == REFUSAL_KIND_INVALID
    assert rig.backend.feed.status().consumers == ()


async def test_a_symbol_that_is_named_twice_has_one_row(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["SPY", "SPX", "SPY"]})
    empty = socket.subscribe(quotes={})
    await rig.pass_time(2)
    assert list(socket.of(number)[0].quotes.rows.symbol) == ["SPY", "SPX"]
    assert socket.of(empty)[0].snapshot and not socket.of(empty)[0].quotes.rows.symbol


async def test_without_broker_data_the_topic_says_why(rig: ApiRig) -> None:
    """GEN-04 end to end: the grant is refused, so tier T1 is absent."""
    await rig.log_in()
    rig.fake.rest.expire_tokens()
    rig.fake.rest.refuse_renewal()
    with pytest.raises(AuthError):
        await rig.backend.broker.equity("SPY")
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.clock.settle()
    (message,) = socket.of(number)
    assert message.refused.kind == REFUSAL_KIND_UNAVAILABLE
    assert message.refused.reason.startswith("the broker refuses its data: ")
    features = (await rig.http.get("/api/features")).json()
    assert features["tiers"][0] == {
        "tier": "T1",
        "present": False,
        "reason": message.refused.reason,
    }
    assert features["features"][0]["show"] == "unavailable"


async def test_a_slow_page_gets_the_union_and_not_a_queue(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["SPY", "AAPL"]})
    await rig.pass_time(3)
    socket.taking.clear()
    await rig.pass_time(20)
    before = len(socket.of(number))
    socket.taking.set()
    await rig.clock.settle()
    assert len(socket.of(number)) - before <= 2  # the one that was under way, and one for the rest
    held = latest(socket, number)
    for symbol in ("SPY", "AAPL"):
        assert held[symbol]["bid"] == rig.backend.market.column(QUOTE, "bid", [symbol])[0]


async def test_a_topic_that_breaks_says_so(rig: ApiRig, monkeypatch: pytest.MonkeyPatch) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(2)

    def broken(*_: object) -> None:
        raise RuntimeError("inside")

    monkeypatch.setattr("s3t.api.topics.changed_symbols", broken)
    await rig.pass_time(2)
    assert socket.of(number)[-1].refused.kind == REFUSAL_KIND_FAILED
    assert rig.backend.feed.status().granted == 0  # and it asks for nothing any more


# ---------------------------------------------------------------------- coming back


async def test_a_client_that_comes_back_gets_fresh_snapshots(rig: ApiRig) -> None:
    """API-03, and the definition of done of WP-0.7."""
    await rig.log_in()
    stays = await rig.socket()
    stays.subscribe(quotes={"symbol": ["SPY"]})
    first = await rig.socket()
    status, quotes = first.subscribe(status={}), first.subscribe(quotes={"symbol": ["SPY", "SPX"]})
    await rig.pass_time(3)
    old = latest(first, quotes)
    assert old["SPY"]["bid"] > 0 and old["SPX"]["last"] > 0
    await first.close()
    await rig.pass_time(30)  # the market goes on without it

    again = await rig.socket()
    assert again.subscribe(status={}) == status  # the numbers begin anew
    assert again.subscribe(quotes={"symbol": ["SPY", "SPX"]}) == quotes
    await rig.clock.settle()
    for number in (status, quotes):
        assert again.of(number)[0].snapshot
    rows = again.of(quotes)[0].quotes.rows
    current = rig.backend.market.column(QUOTE, "bid", ["SPY"])[0]
    assert rows.bid[0] == current and rows.quote_received_us[0] > old["SPY"]["quote_received_us"]
    # SPX was held by nobody in between. No old value stands in for it, and it comes back.
    assert math.isnan(rows.last[1])
    assert again.of(status)[0].status.data_time_us == to_micros(rig.clock.wall())
    await rig.pass_time(2)
    assert latest(again, quotes)["SPX"]["last"] > 0


async def test_nothing_of_a_connection_stays(rig: ApiRig) -> None:
    """Demand, takers and readers of a page that is gone; over many of them."""
    await rig.log_in()
    feed, market = rig.backend.feed, rig.backend.market
    connections = rig.app._api._app.state.connections  # type: ignore[attr-defined]
    readers = len(feed._readers)
    for _ in range(20):
        socket = await rig.socket()
        socket.subscribe(status={})
        socket.subscribe(quotes={"symbol": ["SPY", "NOSUCH"]})
        await rig.clock.settle(20)
        assert len(connections) == 1
        await socket.close()
    await rig.pass_time(2)
    assert len(connections) == 0
    assert feed.status().consumers == () and feed.status().granted == 0
    assert market._takers == [] and len(feed._readers) == readers
    assert rig.fake.dxlink.subscriptions() == set()


async def test_the_values_after_a_loss_of_the_broker(rig: ApiRig) -> None:
    """The page holds the rows, and the status says from when on they are live (UI-05)."""
    await rig.log_in()
    socket = await rig.socket()
    status, quotes = socket.subscribe(status={}), socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(3)

    def live() -> bool:
        since = socket.of(status)[-1].status
        received = latest(socket, quotes)["SPY"]["quote_received_us"]
        return since.HasField("live_since_us") and received >= since.live_since_us

    assert live()
    rig.fake.dxlink.drop()
    await rig.clock.settle()
    bid = latest(socket, quotes)["SPY"]["bid"]
    assert not live() and bid > 0  # the last value, and it is known not to be live
    await rig.pass_time(5)
    assert live()


async def test_json_says_what_the_bytes_say(rig: ApiRig) -> None:
    """API-04: the switch is the client's, and the messages are the same."""
    await rig.log_in()
    rig.fake.dxlink.silent = True
    binary, text = await rig.socket(), await rig.socket(subprotocol="s3t.v1.json")
    for socket in (binary, text):
        assert socket.accepted is not None
        assert socket.accepted["subprotocol"] == socket.subprotocol
        socket.subscribe(status={})
        socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(3)
    assert all(isinstance(data, bytes) for data in binary.raw)
    assert all(isinstance(data, str) for data in text.raw)
    for number in (1, 2):
        assert str(binary.of(number)[0]) == str(text.of(number)[0])
    assert len(binary.of(2)) == len(text.of(2))


async def test_a_message_that_cannot_be_read_ends_the_websocket(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    socket.send("text on a connection of bytes")
    await asyncio.wait_for(socket.task, 5)
    assert socket.bodies() == ["bye"] and socket.closed == 1000
