"""The DXLink streaming session (BRK-02, BRK-03, BRK-06).

Source: `tt` tt_capture.py, class ``DXLinkFeed``, version 1.6.0 (`tt` is not a git
repository, so no commit can be named). Kept from it: SETUP, then AUTH after the server's
first ``UNAUTHORIZED``; one channel per event type; the COMPACT format with explicit field
lists, the same lists as the official SDK; the server's field order is the one used for
decoding; a keepalive every 30 s; subscription messages of 100.

The session does the technical work and holds no content (decided 2026-11-23, requirements
section 3):

- **It stays up.** A loop opens the session again after any loss: a closed socket, no
  message for 60 s, a stall, a refused token, a protocol error. The waits between attempts
  are exponential with jitter, from 1 s to 30 s, without an attempt limit.
- **It keeps no subscription set.** ``subscribe`` and ``unsubscribe`` send their pairs now,
  on the current connection, and are refused while the session is not connected. After a
  reconnect the session is open and empty. The subscription manager (``s3t.subs``) knows
  what is wanted, paces the changes and sends them again.
- **A hard cap.** The session counts the pairs sent on the current connection and refuses a
  call that would go beyond the configured limit. It is a backstop.
- **All six channels open with the session,** before it says ``connected``, so a subscribe
  call never waits for the server.
- **The application's own records.** Each data block becomes an :class:`EventBatch` of
  columns (``s3t.broker.tastytrade.convert``), with the receipt time taken before decoding.
- **Stall detection** (BRK-03). No data for ``stall_after`` seconds, while data is expected
  and something is subscribed, makes the session ``stalled`` and drops it. The clock starts
  when the first pair is sent on a connection. Each stall in a row doubles the wait, up to
  15 minutes, so a quiet symbol does not cause a reconnect a minute; data that arrives well
  after the last subscription was sent resets it.
- **The gap is reported** (BRK-02): a :class:`Gap` item when a session is open again,
  before the phase ``connected`` and before any of its data.
- **Rate errors.** DXLink does not confirm a subscription. When the server says the rate is
  too high, the session passes that on as a notice with the event type of the channel. The
  manager slows down and sends again what it sent just before.
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
from collections.abc import Awaitable, Callable, Collection
from datetime import UTC, datetime, timedelta
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
    StreamRefused,
    StreamSink,
    StreamState,
    StreamStatus,
    Sub,
)
from s3t.broker.limits import Backoff, Clock, Sleep
from s3t.broker.tastytrade.convert import EVENT_FIELDS, EVENT_OF, WIRE_NAME, Decoder, wire_symbol
from s3t.broker.tastytrade.ws import Connect, Connection, ConnectionLost, open_websocket
from s3t.secrets import redact

DXLINK_VERSION = "0.1-DXF-JS/0.3.0"  # as `tt` sends it
KEEPALIVE_S = 30.0
KEEPALIVE_TIMEOUT_S = 60
SILENCE_DROP_S = 60.0
HANDSHAKE_TIMEOUT_S = 20.0
CHANNEL_TIMEOUT_S = 15.0
WATCH_PERIOD_S = 5.0
MESSAGE_SIZE = 100
MAX_STALL_WAIT_S = 900.0
LIVELY_AFTER_S = 5.0
STEADY_AFTER_S = 30.0
TOKEN_MARGIN = timedelta(minutes=5)

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
        limit: int = 11_000,
        candle_limit: int = 100,
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
        self._limit, self._candle_limit = limit, candle_limit
        self._backoff = backoff or Backoff()
        self._rng = rng or random.Random()
        # what the current connection was sent: counts only, never the pairs
        self._subscribed = 0
        self._candles = 0
        self._first_sub: float | None = None
        self._last_sub = clock()
        self._sending = asyncio.Lock()
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
        self._decoders: dict[EventType, Decoder] = {}
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
        self._stalled = False
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
        self.ignored_events = 0
        """Candle events left out for a missing time or an unknown symbol form."""

    def __repr__(self) -> str:
        return f"DxLinkSession(phase={self._phase!r}, subscribed={self._subscribed})"

    # ------------------------------------------------------------------ Stream

    def start(self) -> None:
        if self._main is None and not self._closed:
            self._main = asyncio.create_task(self._run(), name="dxlink-session")

    async def subscribe(self, subs: Collection[Sub]) -> None:
        subs = list(subs)
        if not subs:
            return
        async with self._sending:
            self._must_be_connected()
            candles = sum(1 for sub in subs if sub.event is EventType.CANDLE)
            if self._subscribed + len(subs) > self._limit:
                raise StreamRefused(
                    f"{len(subs)} subscriptions would go beyond the cap of {self._limit}",
                    reason="limit",
                )
            if self._candles + candles > self._candle_limit:
                raise StreamRefused(
                    f"{candles} candles would go beyond the cap of {self._candle_limit}",
                    reason="candle_limit",
                )
            await self._send_changes("add", subs)

    async def unsubscribe(self, subs: Collection[Sub]) -> None:
        subs = list(subs)
        if not subs:
            return
        async with self._sending:
            self._must_be_connected()
            await self._send_changes("remove", subs)

    def _must_be_connected(self) -> None:
        if self._phase != "connected" or self._conn is None:
            raise StreamRefused(
                f"the session is {self._phase}, not connected", reason="not_connected"
            )

    async def _send_changes(self, kind: str, subs: list[Sub]) -> None:
        """Send the pairs in messages of 100 per event type, and count them."""
        by_event: dict[EventType, list[Sub]] = {}
        for sub in subs:
            by_event.setdefault(sub.event, []).append(sub)
        add = kind == "add"
        for event, group in by_event.items():
            channel = self._channels[event]
            for i in range(0, len(group), MESSAGE_SIZE):
                chunk = group[i : i + MESSAGE_SIZE]
                entries = [_add_entry(s) if add else _entry(s) for s in chunk]
                try:
                    message = {"type": "FEED_SUBSCRIPTION", "channel": channel, kind: entries}
                    await self._send(message)
                except (ConnectionLost, _SessionEnded) as e:
                    self._fail(str(e))
                    raise StreamRefused(
                        "the session was lost while sending", reason="not_connected"
                    ) from None
                candles = len(chunk) if event is EventType.CANDLE else 0
                if add:
                    now = self._clock()
                    if self._subscribed == 0:
                        self._first_sub = now
                    self._last_sub = now
                    self._subscribed += len(chunk)
                    self._candles += candles
                else:
                    self._subscribed = max(self._subscribed - len(chunk), 0)
                    self._candles = max(self._candles - candles, 0)
                    if self._subscribed == 0:
                        self._first_sub = None

    def status(self) -> StreamStatus:
        return StreamStatus(
            phase=self._phase,
            since=self._phase_since,
            reason=self._reason,
            connected_at=self._connected_at,
            last_data_at=self._last_data_at,
            reconnects=max(self._sessions - 1, 0),
            stalled=self._stalled,
            stalls=self._stalls,
            last_gap=self._last_gap,
            subscribed=self._subscribed,
            candles=self._candles,
            limit=self._limit,
            candle_limit=self._candle_limit,
            aggregation_period=self._granted_period,
            parse_errors=self.parse_errors,
            sink_errors=self.sink_errors,
            ignored_events=self.ignored_events,
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
        self._decoders.clear()
        self._subscribed = self._candles = 0
        self._first_sub = None
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
        await self._open_channels()

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
        self._set_phase("connected")
        self._spawn(self._keepalive(), "dxlink-keepalive")
        self._spawn(self._watch(), "dxlink-watch")
        return await self._failed

    async def _teardown(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        conn, self._conn = self._conn, None
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
            if self._phase != "connected" or self._first_sub is None:
                continue
            quiet = now - max(self._last_data, self._first_sub, self._expected_since)
            if quiet > self._stall_wait:
                self._stalls += 1
                self._stalled = True
                reason = f"no data for {quiet:.0f} s"
                self._stall_wait = min(self._stall_wait * 2.0, MAX_STALL_WAIT_S)
                self._set_phase("stalled", reason)
                raise _SessionEnded(reason)

    # ------------------------------------------------------------------ channels

    async def _open_channels(self) -> None:
        """One channel per event type, opened and set up before the session is connected."""
        for number, event in enumerate(EventType):
            channel = 1 + 2 * number
            self._opened[channel] = asyncio.Event()
            self._channel_events[channel] = event
            await self._send(
                {
                    "type": "CHANNEL_REQUEST",
                    "channel": channel,
                    "service": "FEED",
                    "parameters": {"contract": "AUTO"},
                }
            )
        for channel, opened in self._opened.items():
            await self._until(opened, CHANNEL_TIMEOUT_S, f"channel {channel} not opened")
        for channel, event in self._channel_events.items():
            await self._send(
                {
                    "type": "FEED_SETUP",
                    "channel": channel,
                    "acceptAggregationPeriod": self._aggregation,
                    "acceptDataFormat": "COMPACT",
                    "acceptEventFields": {WIRE_NAME[event]: list(EVENT_FIELDS[event])},
                }
            )
            self._channels[event] = channel

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
                event = EVENT_OF.get(name)
                if event is None or not isinstance(fields, list):
                    continue
                if tuple(fields) != EVENT_FIELDS[event] and event not in self._fields:
                    LOG.info("stream: the server set its own field list for %s", name)
                self._fields[event] = tuple(str(f) for f in fields)
                decoder = self._decoders[event] = Decoder(event, self._fields[event])
                if decoder.missing:
                    LOG.warning("stream: the server leaves out fields of %s", name)
        elif kind == "ERROR":
            text = redact(str(message.get("message", "")))[:200]
            code = str(message.get("error", ""))
            event = self._channel_events.get(channel)
            if "rate" in text.lower():
                LOG.warning("stream: %r on channel %s", text, channel)
                self._emit(StreamNotice(self._next(), at, "rate", text, event))
            else:
                refused = code in ("BAD_ACTION", "UNAUTHORIZED", "UNSUPPORTED_PROTOCOL")
                LOG.warning("stream: error %s on channel %s: %s", code, channel, text)
                kind_of = "refused" if refused else "error"
                self._emit(StreamNotice(self._next(), at, kind_of, f"{code}: {text}", event))
        elif kind == "CHANNEL_CLOSED":
            raise _SessionEnded(f"channel {channel} closed by the server")

    def _on_data(self, data: list[Any], received_at: datetime) -> None:
        if not data:
            return
        if isinstance(data[0], dict):  # the FULL format, should a server choose it
            blocks: dict[str, list[Any]] = {}
            for ev in data:
                name = ev.get("eventType")
                known = EVENT_OF.get(name)
                names = () if known is None else self._fields.get(known, ())
                blocks.setdefault(name, []).extend(ev.get(f) for f in names)
            data = [x for pair in blocks.items() for x in pair]
        delivered = False
        for i in range(0, len(data) - 1, 2):
            name, values = data[i], data[i + 1]
            event = EVENT_OF.get(name) if isinstance(name, str) else None
            decoder = None if event is None else self._decoders.get(event)
            columns = None
            if event is not None and decoder is not None and isinstance(values, list):
                columns = decoder(values)
            if event is None or columns is None:
                self.parse_errors += 1
                if self.parse_errors <= 5:
                    LOG.warning("stream: cannot decode a block of %s", redact(str(name))[:40])
                continue
            ignored = len(values) // decoder.width - len(columns[0]) if decoder else 0
            if ignored:
                self.ignored_events += ignored
                if self.ignored_events - ignored < 5:
                    LOG.warning("stream: %d %s event(s) ignored", ignored, event.value)
            if not columns[0]:
                continue
            self._last_batch_seq = self._next()
            self._emit(EventBatch(self._last_batch_seq, event, received_at, columns))
            delivered = True
        if delivered:
            now = self._clock()
            self._last_data, self._last_data_at = now, received_at
            self._stalled = False
            if self._phase == "connected" and now - self._last_sub >= LIVELY_AFTER_S:
                self._stall_wait = self._stall_after


def _entry(sub: Sub) -> dict[str, Any]:
    return {"type": WIRE_NAME[sub.event], "symbol": wire_symbol(sub)}


def _add_entry(sub: Sub) -> dict[str, Any]:
    entry = _entry(sub)
    if sub.event is EventType.CANDLE:
        start = sub.from_time or datetime.now(UTC)
        entry["fromTime"] = round(start.timestamp() * 1000)
    return entry
