"""The fake DXLink server over the synthetic market (implementation plan 3.3).

It speaks the part of the DXLink protocol that the session uses: SETUP, AUTH, KEEPALIVE,
CHANNEL_REQUEST, FEED_SETUP, FEED_SUBSCRIPTION with ``add``, ``remove`` and ``reset``, and
FEED_DATA in the COMPACT format. The session reaches it through an in-memory connection
(:meth:`FakeDxLink.connect`), so nothing listens on a port. :meth:`FakeDxLink.serve` puts
the same server behind a real WebSocket, for the one test of the WebSocket adapter.

A message is answered inside the call that delivers it. Data goes out in :meth:`tick`. A
test calls it, or starts :meth:`run`, which ticks once a second on the injected clock. A tick
sends an event only when its values changed, so outside the session hours of the synthetic
market the stream is silent, as a real one is on a closed market. Greeks are sent once a
minute, as `tt` saw them live.

Scenarios (implementation plan 3.3), each an attribute or a method:

- :meth:`drop`: every session is closed now. ``refuse_connects``: the next N connection
  attempts fail.
- ``silent``: connected, keepalive answered, no data (a stalled stream).
- ``frozen``: connected, nothing sent at all, not even a keepalive.
- :meth:`expire_tokens`: the tokens issued so far are refused from now on.
- ``sub_cap``: a subscription message that would go beyond the cap is refused with an error.
  **Invented:** the live server's answer is not known.
- ``change_limit``: more than N subscription changes in W seconds are refused with the
  message `tt` saw live, "Your subscription rate is too high".
- ``no_bid`` and ``no_ask``: symbols whose quote lacks that side: a price of 0 with the size
  ``"NaN"``, as dxFeed sends it (note B, addendum "WP-0.5"). ``index_without_quote``:
  an index sends trades only.
- ``candle_gaps`` and ``candle_suspects``: history with a hole, and with a bar whose high is
  ten times too large. A clock change needs no switch: bars are stamped in UTC, so the
  session moves by an hour where New York changes its clocks.
- :meth:`hold` and :meth:`release`: data messages are kept back and delivered later, in
  order or reversed (delayed and out-of-order messages).
- ``reverse_fields``: the server sets its own field order, the reverse of the one asked.

Source: the DXLink half of `tt` tests/fake_tt.py 1.6.0, moved onto the synthetic market and
given unsubscription, candles and the scenarios (2026-11-23, WP-0.4). The error texts other
than the rate message are not taken from the live server.
"""

import asyncio
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from s3t.broker.fake.market import DXLINK_URL, UNDERLYINGS, SyntheticMarket
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.fake.ticks import NAN, REMOVE_EVENT, Ticks, parse_candle_symbol
from s3t.broker.limits import Sleep
from s3t.broker.tastytrade.ws import Connection, ConnectionLost

RATE_MESSAGE = "Your subscription rate is too high"  # as `tt` saw it live
SERVER_VERSION = "1.0-fake"
DATA_CHUNK = 200
ON_SUBSCRIBE_ONLY = frozenset({"Summary", "Profile"})


@dataclass
class _Channel:
    event: str | None = None
    fields: list[str] = field(default_factory=list)
    period: float = 1.0


class FakeSession:
    """The server's side of one connection."""

    def __init__(self, server: "FakeDxLink", number: int) -> None:
        self.server = server
        self.number = number
        self.authorized = False
        self.closed = False
        self.channels: dict[int, _Channel] = {}
        self.subs: dict[tuple[str, str], int | None] = {}
        """(event, symbol) to the candle start in milliseconds, or None."""
        self.outbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._last_sent: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._held: list[str] | None = None

    # ------------------------------------------------------------------ sending

    def _put(self, message: dict[str, Any]) -> None:
        if self.closed or self.server.frozen:
            return
        text = json.dumps(message)
        if message["type"] == "FEED_DATA" and self._held is not None:
            self._held.append(text)
            return
        self.outbox.put_nowait(text)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.outbox.put_nowait(None)

    def _channel_of(self, event: str) -> tuple[int, _Channel] | None:
        for number, channel in self.channels.items():
            if channel.event == event:
                return number, channel
        return None

    def push(self, event: str, docs: list[dict[str, Any]], *, only_changed: bool = False) -> None:
        """Send these events on the channel of their type."""
        found = self._channel_of(event)
        if found is None or self.server.silent:
            return
        number, channel = found
        values: list[Any] = []
        count = 0
        for doc in docs:
            row = tuple(doc.get(name, NAN) for name in channel.fields)
            key = (event, doc["eventSymbol"])
            if only_changed and self._last_sent.get(key) == row:
                continue
            self._last_sent[key] = row
            values.extend(row)
            count += 1
            if count == DATA_CHUNK:
                self._put({"type": "FEED_DATA", "channel": number, "data": [event, values]})
                values, count = [], 0
        if values:
            self._put({"type": "FEED_DATA", "channel": number, "data": [event, values]})

    # ------------------------------------------------------------------ receiving

    def receive(self, text: str) -> None:
        if self.closed:
            raise ConnectionLost("fake stream: the session is closed")
        message = json.loads(text)
        self.server.received.append(message)
        if self.server.frozen:
            return
        kind, channel = message.get("type"), message.get("channel", 0)
        if kind == "SETUP":
            self._put(
                {
                    "type": "SETUP",
                    "channel": 0,
                    "version": SERVER_VERSION,
                    "keepaliveTimeout": 60,
                    "acceptKeepaliveTimeout": 60,
                }
            )
            self._put({"type": "AUTH_STATE", "channel": 0, "state": "UNAUTHORIZED"})
        elif kind == "AUTH":
            self.authorized = self.server.token_valid(message.get("token"))
            state = "AUTHORIZED" if self.authorized else "UNAUTHORIZED"
            self._put({"type": "AUTH_STATE", "channel": 0, "state": state})
        elif kind == "KEEPALIVE":
            self._put({"type": "KEEPALIVE", "channel": 0})
        elif not self.authorized:
            self._error(channel, "UNAUTHORIZED", "Not authorized")
        elif kind == "CHANNEL_REQUEST":
            self.channels[channel] = _Channel()
            self._put(
                {
                    "type": "CHANNEL_OPENED",
                    "channel": channel,
                    "service": "FEED",
                    "parameters": {**message.get("parameters", {}), "subFormat": "LIST"},
                }
            )
        elif kind == "FEED_SETUP":
            self._feed_setup(channel, message)
        elif kind == "FEED_SUBSCRIPTION":
            self._subscription(channel, message)

    def _error(self, channel: int, code: str, text: str) -> None:
        self._put({"type": "ERROR", "channel": channel, "error": code, "message": text})

    def _feed_setup(self, number: int, message: dict[str, Any]) -> None:
        channel = self.channels.get(number)
        accepted = message.get("acceptEventFields") or {}
        if channel is None or len(accepted) != 1:
            self._error(number, "INVALID_MESSAGE", "Unknown channel or no event fields")
            return
        ((event, names),) = accepted.items()
        channel.event = event
        channel.fields = list(reversed(names)) if self.server.reverse_fields else list(names)
        channel.period = max(float(message.get("acceptAggregationPeriod", 1.0)), 0.1)
        self._put(
            {
                "type": "FEED_CONFIG",
                "channel": number,
                "dataFormat": "COMPACT",
                "aggregationPeriod": channel.period,
                "eventFields": {event: channel.fields},
            }
        )

    def _subscription(self, number: int, message: dict[str, Any]) -> None:
        server = self.server
        adds, removes = message.get("add") or [], message.get("remove") or []
        changes = len(adds) + len(removes)
        if not server.count_changes(changes):
            server.rejected_changes += changes
            self._error(number, "BAD_ACTION", RATE_MESSAGE)
            return
        if message.get("reset"):
            for key in [k for k in self.subs if self._channel_of(k[0]) == self._on(number)]:
                del self.subs[key]
        new = [a for a in adds if (a["type"], a["symbol"]) not in self.subs]
        held = len(self.subs) - sum((r["type"], r["symbol"]) in self.subs for r in removes)
        if server.sub_cap is not None and held + len(new) > server.sub_cap:
            server.rejected_changes += changes
            self._error(number, "BAD_ACTION", "Subscription limit exceeded")
            return
        server.accepted_changes += changes
        for r in removes:
            self.subs.pop((r["type"], r["symbol"]), None)
            self._last_sent.pop((r["type"], r["symbol"]), None)
        by_event: dict[str, list[str]] = {}
        for a in adds:
            self.subs[(a["type"], a["symbol"])] = a.get("fromTime")
            by_event.setdefault(a["type"], []).append(a["symbol"])
        at = server.now()
        for event, symbols in by_event.items():
            if event == "Candle":
                for symbol in symbols:
                    start = self.subs[(event, symbol)] or 0
                    self.push(event, server.candle_history(symbol, start, at))
            else:
                self.push(event, server.events(event, symbols, at))

    def _on(self, number: int) -> tuple[int, _Channel] | None:
        channel = self.channels.get(number)
        return None if channel is None else (number, channel)

    # ------------------------------------------------------------------ ticking

    def tick(self, at: datetime, greeks: bool) -> None:
        if not self.authorized or self.closed:
            return
        by_event: dict[str, list[str]] = {}
        for event, symbol in self.subs:
            by_event.setdefault(event, []).append(symbol)
        for event, symbols in by_event.items():
            if event in ON_SUBSCRIBE_ONLY or (event == "Greeks" and not greeks):
                continue
            if event == "Candle":
                bars = [self.server.ticks.candle(s, at) for s in symbols]
                docs = [b for b in bars if b is not None]
            else:
                docs = self.server.events(event, symbols, at)
            self.push(event, docs, only_changed=True)

    def hold(self) -> None:
        if self._held is None:
            self._held = []

    def release(self, reverse: bool) -> None:
        held, self._held = self._held or [], None
        for text in reversed(held) if reverse else held:
            if not self.closed:
                self.outbox.put_nowait(text)


class MemoryConnection:
    """The client's end of an in-memory connection to the fake server."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def send(self, text: str) -> None:
        self._session.receive(text)

    async def recv(self) -> str:
        if self._session.closed and self._session.outbox.empty():
            raise ConnectionLost("fake stream: connection closed")
        text = await self._session.outbox.get()
        if text is None:
            self._session.outbox.put_nowait(None)  # every later read fails too
            raise ConnectionLost("fake stream: connection closed by the server")
        return text

    async def close(self) -> None:
        self._session.close()


class FakeDxLink:
    def __init__(
        self,
        market: SyntheticMarket,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        rest: FakeTastytrade | None = None,
        tokens: tuple[str, ...] = (),
        url: str = DXLINK_URL,
        greeks_period: float = 60.0,
    ) -> None:
        self.market = market
        self.ticks = Ticks(market)
        self.now = now
        self.url = url
        self._rest = rest
        self._tokens = set(tokens)
        self._expired: set[str] = set()
        self.greeks_period = greeks_period
        self._last_greeks: datetime | None = None
        self.sessions: list[FakeSession] = []
        self.received: list[dict[str, Any]] = []
        """Every message a client sent, decoded."""
        self.connect_attempts = 0
        self.accepted_changes = 0
        self.rejected_changes = 0
        self._changes: deque[tuple[datetime, int]] = deque()
        # scenarios
        self.refuse_connects = 0
        self.silent = False
        self.frozen = False
        self.sub_cap: int | None = 25_000
        self.change_limit: tuple[int, float] = (10_000, 60.0)
        self.no_bid: set[str] = set()
        self.no_ask: set[str] = set()
        self.index_without_quote = False
        self.candle_gaps: dict[str, tuple[datetime, datetime]] = {}
        self.candle_suspects: dict[str, datetime] = {}
        self.reverse_fields = False

    # ------------------------------------------------------------------ connections

    async def connect(self, url: str) -> Connection:
        """The ``Connect`` of the session, in memory."""
        self.connect_attempts += 1
        if url != self.url:
            raise ConnectionLost("fake stream: not the fake stream's address")
        if self.refuse_connects > 0:
            self.refuse_connects -= 1
            raise ConnectionLost("fake stream: connection refused")
        session = FakeSession(self, len(self.sessions) + 1)
        self.sessions.append(session)
        return MemoryConnection(session)

    async def serve(self, ws: Any) -> None:
        """A handler for ``websockets.serve``: the same server behind a real WebSocket."""
        session = FakeSession(self, len(self.sessions) + 1)
        self.sessions.append(session)

        async def write() -> None:
            while (text := await session.outbox.get()) is not None:
                await ws.send(text)
            await ws.close()

        writer = asyncio.create_task(write(), name="fake-dxlink-writer")
        try:
            async for text in ws:
                session.receive(text if isinstance(text, str) else text.decode())
        except ConnectionLost:
            pass
        finally:
            session.close()
            await writer

    @property
    def session(self) -> FakeSession | None:
        """The latest session that is still open."""
        for session in reversed(self.sessions):
            if not session.closed:
                return session
        return None

    def subscriptions(self) -> set[tuple[str, str]]:
        """What the open session holds: (event, symbol)."""
        session = self.session
        return set() if session is None else set(session.subs)

    # ------------------------------------------------------------------ scenarios

    def drop(self) -> None:
        for session in self.sessions:
            session.close()

    def expire_tokens(self) -> None:
        self._expired |= self._tokens
        if self._rest is not None:
            self._expired |= set(self._rest.quote_tokens)

    def hold(self) -> None:
        for session in self.sessions:
            session.hold()

    def release(self, *, reverse: bool = False) -> None:
        for session in self.sessions:
            session.release(reverse)

    def token_valid(self, token: object) -> bool:
        if not isinstance(token, str) or token in self._expired:
            return False
        return token in self._tokens or (
            self._rest is not None and token in self._rest.quote_tokens
        )

    def count_changes(self, n: int) -> bool:
        """Count subscription changes against the limit; False when they go beyond it."""
        limit, window = self.change_limit
        at = self.now()
        while self._changes and (at - self._changes[0][0]).total_seconds() >= window:
            self._changes.popleft()
        if sum(count for _, count in self._changes) + n > limit:
            return False
        self._changes.append((at, n))
        return True

    # ------------------------------------------------------------------ data

    def events(self, event: str, symbols: list[str], at: datetime) -> list[dict[str, Any]]:
        if event == "Quote" and self.index_without_quote:
            symbols = [s for s in symbols if getattr(UNDERLYINGS.get(s), "kind", "") != "index"]
        docs = self.ticks.events(event, symbols, at)
        if event == "Quote":
            for doc in docs:
                if doc["eventSymbol"] in self.no_bid:
                    doc["bidPrice"], doc["bidSize"] = 0.0, NAN
                if doc["eventSymbol"] in self.no_ask:
                    doc["askPrice"], doc["askSize"] = 0.0, NAN
        return docs

    def candle_history(self, symbol: str, start_ms: int, at: datetime) -> list[dict[str, Any]]:
        start = datetime.fromtimestamp(max(start_ms, 0) / 1000, UTC)
        bars = self.ticks.candles(symbol, start, at)
        parsed = parse_candle_symbol(symbol)
        base = symbol if parsed is None else parsed[0]
        gap = self.candle_gaps.get(base)
        if gap is not None:
            lo, hi = (g.timestamp() * 1000 for g in gap)
            bars = [b for b in bars if b["eventFlags"] & REMOVE_EVENT or not lo <= b["time"] < hi]
        suspect = self.candle_suspects.get(base)
        if suspect is not None:
            for bar in bars:
                if bar["time"] == round(suspect.timestamp() * 1000) and bar["high"] != NAN:
                    bar["high"] = round(bar["high"] * 10, 2)
        return bars

    def tick(self) -> None:
        """Send the events whose values changed since they were last sent."""
        at = self.now()
        greeks = (
            self._last_greeks is None
            or (at - self._last_greeks).total_seconds() >= self.greeks_period
        )
        if greeks:
            self._last_greeks = at
        for session in self.sessions:
            session.tick(at, greeks)

    async def run(self, sleep: Sleep = asyncio.sleep, period: float = 1.0) -> None:
        """Tick for ever, once a period. Cancel the task to stop it."""
        while True:
            await sleep(period)
            self.tick()
