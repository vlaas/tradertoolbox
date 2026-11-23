"""The DXLink streaming session (BRK-02, BRK-03, BRK-06; the data of SUB-02).

Source: `tt` tt_capture.py, class ``DXLinkFeed``, version 1.6.0 (`tt` is not a git
repository, so no commit can be named). Kept from it: SETUP, then AUTH after the server's
first ``UNAUTHORIZED``; one channel per event type; the COMPACT format with explicit field
lists, the same lists as the official SDK; the server's field order is the one used for
decoding; a keepalive every 30 s; subscription changes in chunks of 100, paced
(``s3t.broker.limits.ChangePacer``); a pause and half the rate when the server says the rate
is too high.

Changed in the port (2026-11-23, WP-0.4):

- **It stays up.** A loop opens the session again after any loss: a closed socket, no
  message for 60 s, a stall, a refused token, a protocol error. The waits between attempts
  are exponential with jitter, from 1 s to 30 s, without an attempt limit.
- **It holds the wanted set.** ``subscribe`` and ``unsubscribe`` change the set and return.
  One task sends the difference between the set and what this session was sent, removals
  first. After a reconnect nothing was sent, so the whole set goes out again (BRK-02). A
  pair added and removed before its turn is never sent.
- **Unsubscription and candles**, which `tt` did not have.
- **No cache and no tape.** Each data block becomes an :class:`EventBatch` for the sink, with
  the values as received and the receipt time taken before decoding.
- **Stall detection** (BRK-03). No data for ``stall_after`` seconds, while data is expected
  and something is subscribed, makes the session ``stalled`` and drops it. Each stall in a
  row doubles the wait, up to 15 minutes, so a quiet symbol does not cause a reconnect a
  minute; data that arrives well after the subscriptions were sent resets it.
- **The gap is reported** (BRK-02): a :class:`Gap` item when a session is open again, before
  any of its data, then the phase ``resubscribing``, then ``connected``.
- **Rate errors.** DXLink does not confirm a subscription. `tt` found the refused ones by the
  data that never came. Here every change sent on that channel in the 2 s before the error
  is sent again after the pause, behind the changes that still wait. Sending an accepted
  one twice is harmless.
- **A new quote token** is fetched when the server refuses the held one, and when less than
  five minutes of its life are left at a reconnect.
- The clock, the sleep and the connection are injected. Nothing is logged of a message but
  its type and channel.

Not ported: the ``Underlying`` and ``TheoPrice`` events, the wire log, ``reconcile``.
"""

import asyncio
import json
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Collection
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import Any
from zoneinfo import ZoneInfo

from s3t.broker.interface import (
    BrokerError,
    EventBatch,
    EventType,
    Gap,
    QuoteToken,
    StreamItem,
    StreamNotice,
    StreamPhase,
    StreamSink,
    StreamState,
    StreamStatus,
    Sub,
)
from s3t.broker.limits import Backoff, ChangePacer, Clock, Sleep
from s3t.broker.tastytrade.ws import Connect, Connection, ConnectionLost, open_websocket
from s3t.secrets import redact

DXLINK_VERSION = "0.1-DXF-JS/0.3.0"  # as `tt` sends it
KEEPALIVE_S = 30.0
KEEPALIVE_TIMEOUT_S = 60
SILENCE_DROP_S = 60.0
HANDSHAKE_TIMEOUT_S = 20.0
CHANNEL_TIMEOUT_S = 15.0
WATCH_PERIOD_S = 5.0
RESEND_WINDOW_S = 2.0
MAX_STALL_WAIT_S = 900.0
LIVELY_AFTER_S = 5.0
STEADY_AFTER_S = 30.0
TOKEN_MARGIN = timedelta(minutes=5)

# The fields asked per event. The first six lists are `tt`'s, which are the official SDK's.
EVENT_FIELDS: dict[EventType, tuple[str, ...]] = {
    EventType.QUOTE: (
        "eventSymbol", "eventTime", "sequence", "timeNanoPart", "bidTime", "bidExchangeCode",
        "askTime", "askExchangeCode", "bidPrice", "askPrice", "bidSize", "askSize",
    ),
    EventType.TRADE: (
        "eventSymbol", "eventTime", "time", "timeNanoPart", "sequence", "exchangeCode",
        "dayId", "tickDirection", "extendedTradingHours", "price", "change", "size",
        "dayVolume", "dayTurnover",
    ),
    EventType.SUMMARY: (
        "eventSymbol", "eventTime", "dayId", "dayClosePriceType", "prevDayId",
        "prevDayClosePriceType", "openInterest", "dayOpenPrice", "dayHighPrice",
        "dayLowPrice", "dayClosePrice", "prevDayClosePrice", "prevDayVolume",
    ),
    EventType.GREEKS: (
        "eventSymbol", "eventTime", "eventFlags", "index", "time", "sequence", "price",
        "volatility", "delta", "gamma", "theta", "rho", "vega",
    ),
    EventType.PROFILE: (
        "eventSymbol", "eventTime", "description", "shortSaleRestriction", "tradingStatus",
        "haltStartTime", "haltEndTime", "exDividendDayId", "statusReason",
        "high52WeekPrice", "low52WeekPrice", "beta", "shares", "highLimitPrice",
        "lowLimitPrice", "earningsPerShare", "exDividendAmount", "dividendFrequency",
        "freeFloat",
    ),
    EventType.CANDLE: (
        "eventSymbol", "eventTime", "eventFlags", "index", "time", "sequence", "count",
        "open", "high", "low", "close", "volume", "vwap", "bidVolume", "askVolume",
        "impVolatility", "openInterest",
    ),
}  # fmt: skip
_EVENTS = {e.value: e for e in EventType}
_NY = ZoneInfo("America/New_York")

LOG = logging.getLogger(__name__)

type TokenSource = Callable[[], Awaitable[QuoteToken]]


def regular_hours(at: datetime) -> bool:
    """Weekdays from 09:30 to 16:00 in New York, without holidays. The stand-in for the
    exchange calendar of WP-0.6 in the stall rule (BRK-03)."""
    local = at.astimezone(_NY)
    minutes = local.hour * 60 + local.minute
    return local.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60


class _SessionEnded(Exception):
    """The current session is over; the reason is the message."""


class DxLinkSession:
    """The Tastytrade implementation of :class:`s3t.broker.interface.Stream`."""

    def __init__(
        self,
        token_source: TokenSource,
        sink: StreamSink,
        *,
        connect: Connect = open_websocket,
        aggregation_period: float = 0.5,
        stall_after: float = 60.0,
        data_expected: Callable[[datetime], bool] = regular_hours,
        pacer: ChangePacer | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        backoff: Backoff | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._token_source, self._sink, self._connect = token_source, sink, connect
        self._aggregation, self._stall_after = aggregation_period, stall_after
        self._data_expected = data_expected
        self._clock, self._sleep, self._now = clock, sleep, now
        self._pacer = pacer or ChangePacer(clock=clock, sleep=sleep)
        self._backoff = backoff or Backoff()
        self._rng = rng or random.Random()
        # what is wanted, and what this session was sent
        self._held: dict[Sub, None] = {}
        self._sent: set[Sub] = set()
        self._adds: dict[Sub, None] = {}
        self._removes: dict[Sub, None] = {}
        self._recent: deque[tuple[float, int, tuple[Sub, ...], tuple[Sub, ...]]] = deque()
        self._wake = asyncio.Event()
        self._synced = asyncio.Event()
        # the session
        self._token: QuoteToken | None = None
        self._conn: Connection | None = None
        self._failed: asyncio.Future[str] | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._main: asyncio.Task[None] | None = None
        self._authorized = asyncio.Event()
        self._auth_sent = False
        self._channels: dict[EventType, int] = {}
        self._channel_events: dict[int, EventType] = {}
        self._opened: dict[int, asyncio.Event] = {}
        self._fields: dict[EventType, tuple[str, ...]] = {}
        self._granted_period: float | None = None
        # state and counters
        self._seq = 0
        self._phase: StreamPhase = "idle"
        self._phase_since: datetime | None = None
        self._phase_clock = clock()
        self._reason: str | None = None
        self._closed = False
        self._sessions = 0
        self._stalls = 0
        self._stall_wait = stall_after
        self._expected_since: float | None = None
        self._last_message = clock()
        self._last_data = clock()
        self._last_data_at: datetime | None = None
        self._last_batch_seq = 0
        self._connected_at: datetime | None = None
        self._lost: tuple[int, datetime] | None = None
        self._last_gap: Gap | None = None
        self.parse_errors = 0
        self.sink_errors = 0

    def __repr__(self) -> str:
        return f"DxLinkSession(phase={self._phase!r}, held={len(self._held)})"

    # ------------------------------------------------------------------ Stream

    def start(self) -> None:
        if self._main is None and not self._closed:
            self._main = asyncio.create_task(self._run(), name="dxlink-session")

    def subscribe(self, subs: Collection[Sub]) -> None:
        for sub in subs:
            if sub.from_time is not None and sub.event is not EventType.CANDLE:
                raise ValueError("only a candle subscription has a start time")
            if sub in self._held:
                continue
            self._held[sub] = None
            if sub in self._sent:
                self._removes.pop(sub, None)
            else:
                self._adds[sub] = None
        self._changed()

    def unsubscribe(self, subs: Collection[Sub]) -> None:
        for sub in subs:
            if sub not in self._held:
                continue
            del self._held[sub]
            if sub in self._sent:
                self._removes[sub] = None
            else:
                self._adds.pop(sub, None)
        self._changed()

    def _changed(self) -> None:
        if self._adds or self._removes:
            self._synced.clear()
            self._wake.set()

    async def synced(self) -> None:
        await self._synced.wait()

    def status(self) -> StreamStatus:
        return StreamStatus(
            phase=self._phase,
            since=self._phase_since,
            reason=self._reason,
            connected_at=self._connected_at,
            last_data_at=self._last_data_at,
            reconnects=max(self._sessions - 1, 0),
            stalls=self._stalls,
            last_gap=self._last_gap,
            held=len(self._held),
            pending=len(self._adds) + len(self._removes),
            changes_last_minute=self._pacer.used(),
            change_limit=self._pacer.window_cap,
            rate_errors=self._pacer.rate_errors,
            aggregation_period=self._granted_period,
        )

    async def aclose(self) -> None:
        self._closed = True
        if self._main is not None:
            self._main.cancel()
            await asyncio.gather(self._main, return_exceptions=True)
            self._main = None
        if self._phase != "closed":
            self._set_phase("closed")

    # ------------------------------------------------------------------ items

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def _emit(self, item: StreamItem) -> None:
        try:
            self._sink(item)
        except Exception:
            self.sink_errors += 1
            if self.sink_errors <= 5:
                LOG.exception("stream: the sink failed on a %s item", type(item).__name__)

    def _set_phase(self, phase: StreamPhase, reason: str | None = None) -> None:
        at = self._now()
        self._phase, self._phase_since, self._phase_clock = phase, at, self._clock()
        if reason is not None or phase == "connecting":
            self._reason = reason if reason is not None else self._reason
        self._emit(StreamState(self._next(), phase, at, reason))
        log = LOG.warning if phase in ("disconnected", "stalled") else LOG.info
        log("stream: %s%s", phase, f" ({reason})" if reason else "")

    # ------------------------------------------------------------------ the loop

    async def _run(self) -> None:
        attempt = 0
        while not self._closed:
            self._set_phase("connecting")
            opened = self._clock()
            try:
                await self._open()
                reason = await self._serve()
            except _SessionEnded as e:
                reason = str(e)
            except (ConnectionLost, BrokerError) as e:
                reason = redact(f"{type(e).__name__}: {e}")
            finally:
                await self._teardown()
            if self._clock() - opened >= STEADY_AFTER_S:
                attempt = 0
            self._set_phase("disconnected", reason)
            wait = self._backoff.delay(min(attempt, 30), self._rng)
            attempt += 1
            await self._sleep(wait)

    async def _open(self) -> None:
        token = self._token
        if token is None or token.expires_at - self._now() < TOKEN_MARGIN:
            token = self._token = await self._token_source()
        self._failed = asyncio.get_running_loop().create_future()
        self._authorized.clear()
        self._auth_sent = False
        self._channels.clear()
        self._channel_events.clear()
        self._opened.clear()
        self._fields.clear()
        self._recent.clear()
        self._conn = await self._connect(token.url)
        self._last_message = self._clock()
        self._spawn(self._read(self._conn), "dxlink-reader")
        await self._send(
            {
                "type": "SETUP",
                "channel": 0,
                "keepaliveTimeout": KEEPALIVE_TIMEOUT_S,
                "acceptKeepaliveTimeout": KEEPALIVE_TIMEOUT_S,
                "version": DXLINK_VERSION,
            }
        )
        await self._until(self._authorized, HANDSHAKE_TIMEOUT_S, "not authorized")

    async def _serve(self) -> str:
        assert self._failed is not None
        at = self._now()
        self._sessions += 1
        self._connected_at = at
        self._last_data = self._clock()
        self._expected_since = None
        if self._lost is not None:
            last_seq, since = self._lost
            self._lost = None
            gap = Gap(self._next(), last_seq, since, at, self._reason or "lost")
            self._last_gap = gap
            self._emit(gap)
        self._sent.clear()
        self._removes.clear()
        self._adds = dict.fromkeys(self._held)
        self._set_phase("resubscribing" if self._held else "connected")
        self._spawn(self._sync(), "dxlink-sync")
        self._spawn(self._keepalive(), "dxlink-keepalive")
        self._spawn(self._watch(), "dxlink-watch")
        return await self._failed

    async def _teardown(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        conn, self._conn = self._conn, None
        self._synced.clear()
        if conn is not None:
            if self._authorized.is_set() and self._lost is None:
                since = self._last_data_at if self._last_batch_seq else None
                self._lost = (self._last_batch_seq, since or self._now())
            try:
                await conn.close()
            except Exception as e:
                LOG.debug("stream: close failed: %s", type(e).__name__)

    def _spawn(self, work: Awaitable[None], name: str) -> None:
        async def guarded() -> None:
            try:
                await work
            except _SessionEnded as e:
                self._fail(str(e))
            except ConnectionLost as e:
                self._fail(str(e))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                LOG.exception("stream: %s failed", name)
                self._fail(f"internal error in {name}: {type(e).__name__}")

        self._tasks.append(asyncio.create_task(guarded(), name=name))

    def _fail(self, reason: str) -> None:
        if self._failed is not None and not self._failed.done():
            self._failed.set_result(redact(reason))

    async def _until(self, event: asyncio.Event, seconds: float, what: str) -> None:
        """Wait for an event of the session, at most this many seconds of the clock."""
        assert self._failed is not None
        waiters = [asyncio.ensure_future(event.wait()), asyncio.ensure_future(self._sleep(seconds))]
        try:
            await asyncio.wait([*waiters, self._failed], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
        if event.is_set():
            return
        if self._failed.done():
            raise _SessionEnded(self._failed.result())
        raise _SessionEnded(f"{what} within {seconds:.0f} s")

    async def _send(self, message: dict[str, Any]) -> None:
        if self._conn is None:
            raise _SessionEnded("no connection")
        LOG.debug("stream -> %s channel %s", message["type"], message.get("channel"))
        await self._conn.send(json.dumps(message, separators=(",", ":")))

    # ------------------------------------------------------------------ timers

    async def _keepalive(self) -> None:
        while True:
            await self._sleep(KEEPALIVE_S)
            await self._send({"type": "KEEPALIVE", "channel": 0})

    async def _watch(self) -> None:
        while True:
            await self._sleep(WATCH_PERIOD_S)
            now = self._clock()
            if now - self._last_message > SILENCE_DROP_S:
                raise _SessionEnded(f"no message for {SILENCE_DROP_S:.0f} s")
            if not self._data_expected(self._now()):
                self._expected_since = None
                continue
            if self._expected_since is None:
                self._expected_since = now
            if self._phase != "connected" or not self._sent:
                continue
            quiet = now - max(self._last_data, self._phase_clock, self._expected_since)
            if quiet > self._stall_wait:
                self._stalls += 1
                reason = f"no data for {quiet:.0f} s"
                self._stall_wait = min(self._stall_wait * 2.0, MAX_STALL_WAIT_S)
                self._set_phase("stalled", reason)
                raise _SessionEnded(reason)

    # ------------------------------------------------------------------ subscriptions

    def _next_chunk(self) -> tuple[EventType, list[Sub], list[Sub]] | None:
        first = next(iter(self._removes), None) or next(iter(self._adds), None)
        if first is None:
            return None
        event, size = first.event, self._pacer.burst
        removes = list(islice((s for s in self._removes if s.event is event), size))
        adds = list(islice((s for s in self._adds if s.event is event), size - len(removes)))
        return event, adds, removes

    async def _sync(self) -> None:
        while True:
            chunk = self._next_chunk()
            if chunk is None:
                if self._phase == "resubscribing":
                    self._set_phase("connected")
                self._synced.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            event, adds, removes = chunk
            channel = await self._channel(event)
            await self._pacer.acquire(len(adds) + len(removes))
            # The wanted set may have changed during the waits.
            adds = [s for s in adds if s in self._adds]
            removes = [s for s in removes if s in self._removes]
            if not adds and not removes:
                continue
            message: dict[str, Any] = {"type": "FEED_SUBSCRIPTION", "channel": channel}
            if removes:
                message["remove"] = [{"type": event.value, "symbol": s.symbol} for s in removes]
            if adds:
                message["add"] = [_add_entry(s) for s in adds]
            await self._send(message)
            for sub in adds:
                del self._adds[sub]
                self._sent.add(sub)
            for sub in removes:
                del self._removes[sub]
                self._sent.discard(sub)
            now = self._clock()
            self._recent.append((now, channel, tuple(adds), tuple(removes)))
            while self._recent and now - self._recent[0][0] > RESEND_WINDOW_S:
                self._recent.popleft()

    async def _channel(self, event: EventType) -> int:
        """The channel of an event type; opened and set up on first use."""
        if event in self._channels:
            return self._channels[event]
        channel = 1 + 2 * len(self._channels)
        opened = self._opened[channel] = asyncio.Event()
        self._channel_events[channel] = event
        await self._send(
            {
                "type": "CHANNEL_REQUEST",
                "channel": channel,
                "service": "FEED",
                "parameters": {"contract": "AUTO"},
            }
        )
        await self._until(opened, CHANNEL_TIMEOUT_S, f"channel {channel} not opened")
        await self._send(
            {
                "type": "FEED_SETUP",
                "channel": channel,
                "acceptAggregationPeriod": self._aggregation,
                "acceptDataFormat": "COMPACT",
                "acceptEventFields": {event.value: list(EVENT_FIELDS[event])},
            }
        )
        self._channels[event] = channel
        return channel

    def _send_again(self, channel: int) -> None:
        """After a rate error: what was sent on this channel lately may not have counted."""
        for _, sent_on, adds, removes in self._recent:
            if sent_on != channel:
                continue
            for sub in adds:
                if sub in self._held and sub in self._sent:
                    self._sent.discard(sub)
                    self._adds[sub] = None
            for sub in removes:
                if sub not in self._held and sub not in self._sent:
                    self._sent.add(sub)
                    self._removes[sub] = None
        self._recent = deque(r for r in self._recent if r[1] != channel)
        self._changed()

    # ------------------------------------------------------------------ reading

    async def _read(self, conn: Connection) -> None:
        while True:
            raw = await conn.recv()
            received_at = self._now()
            self._last_message = self._clock()
            try:
                message = json.loads(raw)
                kind = message["type"]
            except (ValueError, TypeError, KeyError):
                raise _SessionEnded("protocol error: a message that is not DXLink") from None
            if kind == "FEED_DATA":
                self._on_data(message.get("data") or [], received_at)
            else:
                await self._on_control(kind, message, received_at)

    async def _on_control(self, kind: str, message: dict[str, Any], at: datetime) -> None:
        channel = message.get("channel", 0)
        LOG.debug("stream <- %s channel %s", kind, channel)
        if kind == "AUTH_STATE":
            if message.get("state") == "AUTHORIZED":
                self._authorized.set()
            elif self._auth_sent:
                self._token = None
                raise _SessionEnded("the quote token was refused")
            else:
                self._auth_sent = True
                token = self._token
                assert token is not None
                # _send logs the type and the channel of a message, never its content.
                secret = token.token.get_secret_value()
                await self._send({"type": "AUTH", "channel": 0, "token": secret})
        elif kind == "CHANNEL_OPENED":
            opened = self._opened.get(channel)
            if opened is not None:
                opened.set()
        elif kind == "FEED_CONFIG":
            period = message.get("aggregationPeriod")
            if isinstance(period, int | float):
                self._granted_period = float(period)
            for name, fields in (message.get("eventFields") or {}).items():
                event = _EVENTS.get(name)
                if event is None or not isinstance(fields, list):
                    continue
                if tuple(fields) != EVENT_FIELDS[event] and event not in self._fields:
                    LOG.info("stream: the server set its own field list for %s", name)
                self._fields[event] = tuple(str(f) for f in fields)
        elif kind == "ERROR":
            text = redact(str(message.get("message", "")))[:200]
            code = str(message.get("error", ""))
            if "rate" in text.lower():
                pause = self._pacer.on_rate_error()
                self._send_again(channel)
                if pause:
                    LOG.warning(
                        "stream: %r; changes wait %.0f s, rate now %.0f a second",
                        text,
                        pause,
                        self._pacer.rate,
                    )
                self._emit(StreamNotice(self._next(), at, "rate", text))
            else:
                refused = code in ("BAD_ACTION", "UNAUTHORIZED", "UNSUPPORTED_PROTOCOL")
                LOG.warning("stream: error %s on channel %s: %s", code, channel, text)
                kind_of = "refused" if refused else "error"
                self._emit(StreamNotice(self._next(), at, kind_of, f"{code}: {text}"))
        elif kind == "CHANNEL_CLOSED":
            raise _SessionEnded(f"channel {channel} closed by the server")

    def _on_data(self, data: list[Any], received_at: datetime) -> None:
        if not data:
            return
        if isinstance(data[0], dict):  # the FULL format, should a server choose it
            blocks: dict[str, list[Any]] = {}
            for ev in data:
                name = ev.get("eventType")
                known = _EVENTS.get(name)
                names = () if known is None else self._fields.get(known, ())
                blocks.setdefault(name, []).extend(ev.get(f) for f in names)
            data = [x for pair in blocks.items() for x in pair]
        delivered = False
        for i in range(0, len(data) - 1, 2):
            name, values = data[i], data[i + 1]
            event = _EVENTS.get(name) if isinstance(name, str) else None
            fields = None if event is None else self._fields.get(event)
            if (
                event is None
                or not fields
                or not isinstance(values, list)
                or len(values) % len(fields)
            ):
                self.parse_errors += 1
                if self.parse_errors <= 5:
                    LOG.warning("stream: cannot decode a block of %s", redact(str(name))[:40])
                continue
            if not values:
                continue
            self._last_batch_seq = self._next()
            self._emit(EventBatch(self._last_batch_seq, event, fields, values, received_at))
            delivered = True
        if delivered:
            now = self._clock()
            self._last_data, self._last_data_at = now, received_at
            if self._phase == "connected" and now - self._phase_clock >= LIVELY_AFTER_S:
                self._stall_wait = self._stall_after


def _add_entry(sub: Sub) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": sub.event.value, "symbol": sub.symbol}
    if sub.event is EventType.CANDLE:
        start = sub.from_time or datetime.now(UTC)
        entry["fromTime"] = round(start.timestamp() * 1000)
    return entry
