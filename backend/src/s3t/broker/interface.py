"""The broker interface (GEN-09).

Everything the application asks of a broker is a method of :class:`Broker`, so another broker
can be judged against this list. The list grows with the packages that need it:

- WP-0.3, here: the REST reads of market data. Instruments, option chains (equity and
  futures), market metrics and the streaming token. All are read-only (GEN-01, BRK-08).
- WP-0.4, here: the stream session. Subscribe, unsubscribe, events as plain batches of the
  application's own records, the connection state. Candles come over the stream. Also
  ``source()`` and ``data_time()``, which say what kind of data the broker serves and which
  instant it belongs to (GEN-11).
- R5: the account reads (positions, balances, transactions).

Nothing in the interface places, changes or cancels an order or changes account state, and
nothing may be added that does (GEN-01).

Instruments, chains and metrics are returned as the broker's JSON documents (read-only
mappings) until the package that first uses each one gives it a type. That is WP-0.5 for
instruments and chains (the instrument model), and WP-1.1 and WP-1.2 for the metrics.
Decided 2026-11-22, with a rule: once a document is typed, this interface returns only the
typed record and the broker's JSON stays inside ``s3t.broker.tastytrade``. Until then,
nothing outside ``s3t.broker`` reads broker keys, except the package typing the document.

The stream is typed already (decided 2026-11-23): no field name of the broker's feed and no
value "as received" crosses this interface. The broker module converts every event into the
records below.
"""

import re
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import SecretStr

type JsonObject = Mapping[str, Any]
"""A JSON object as the broker sent it. Treat it as read-only."""


@dataclass(frozen=True)
class QuoteToken:
    """The token and address for the streaming session."""

    url: str
    token: SecretStr
    level: str
    issued_at: datetime
    """UTC."""
    expires_at: datetime
    """UTC. As the response states it (seen live 2026-11-23); 24 hours after issue where a
    response carries none (note B, section 1)."""


type AuthState = Literal["none", "valid", "due", "lapsed", "renewing", "refused"]


@dataclass(frozen=True)
class RestStatus:
    """The state of the REST connection, for the status bar and the health route (BRK-09).

    ``auth`` is one of:

    - ``none``: no access token yet; the first call fetches one.
    - ``valid``: a token is held with more than the renewal margin (60 s) left.
    - ``due``: the token is inside the margin; the next call renews it first.
    - ``lapsed``: the token has expired while nothing was called; the next call renews it.
      This is normal after a quiet spell, because renewal happens on use.
    - ``renewing``: a renewal is under way.
    - ``refused``: the broker refused the grant, or the grant has more than read scope.
      No call is made until the backend is restarted with a new grant; ``refusal`` says why.
    """

    auth: AuthState
    token_expires_at: datetime | None
    """UTC. None without a token."""
    renews_from: datetime | None
    """UTC. From this instant the next call renews the token first."""
    last_renewal_at: datetime | None
    """UTC."""
    renewals: int
    refusal: str | None
    rate: float
    """The configured limit: requests per second on average."""
    burst: int
    """The configured limit: requests at once after a quiet spell."""
    requests_last_minute: int
    """Requests sent in the last 60 s, the token renewals included."""
    held_until: datetime | None
    """UTC. While a 429 holds every request: the end of the hold."""
    rate_limited: int
    """429 answers so far."""


# ---------------------------------------------------------------------- the stream (WP-0.4)


class EventType(StrEnum):
    """The stream events the application asks for. The values are the application's own
    names; a broker module maps them to its wire names (GEN-09)."""

    QUOTE = "quote"
    TRADE = "trade"
    SUMMARY = "summary"
    GREEKS = "greeks"
    PROFILE = "profile"
    CANDLE = "candle"


type PeriodUnit = Literal["minute", "hour", "day", "week", "month"]

_UNIT_TEXT: dict[str, PeriodUnit] = {
    "m": "minute",
    "h": "hour",
    "d": "day",
    "w": "week",
    "mo": "month",
}
_PERIOD_TEXT = re.compile(r"(\d+)(mo|m|h|d|w)")


@dataclass(frozen=True, slots=True)
class CandlePeriod:
    """The length of a candle: 15 minutes, 1 day. A value of the application; the broker
    module builds its own candle symbol from it."""

    count: int
    unit: PeriodUnit

    def __post_init__(self) -> None:
        if self.count < 1 or self.unit not in _UNIT_TEXT.values():
            raise ValueError("a candle period is a count of at least 1 and a known unit")

    def text(self) -> str:
        """``15m``, ``1h``, ``1d``, ``1w``, ``1mo``: the form recordings hold."""
        letter = next(k for k, v in _UNIT_TEXT.items() if v == self.unit)
        return f"{self.count}{letter}"

    @classmethod
    def parse(cls, text: str) -> "CandlePeriod":
        m = _PERIOD_TEXT.fullmatch(text)
        if m is None:
            raise ValueError("not a candle period")
        return cls(int(m[1]), _UNIT_TEXT[m[2]])


@dataclass(frozen=True, slots=True)
class Sub:
    """One subscription: a stream symbol and an event type; for a candle also the period.

    ``symbol`` is the broker's stream symbol (the ``streamer-symbol`` of an instrument).
    Outside the broker module it is an opaque key: taken from the broker, given back to it,
    never parsed or composed.
    """

    symbol: str
    event: EventType
    period: CandlePeriod | None = None
    """Candles only, and required for them."""
    from_time: datetime | None = None
    """Candles only, UTC: history from this instant on; without it, new bars only. It is
    part of the identity, so a changed start is another subscription."""

    def __post_init__(self) -> None:
        candle = self.event is EventType.CANDLE
        if candle and self.period is None:
            raise ValueError("a candle subscription names its period")
        if not candle and (self.period is not None or self.from_time is not None):
            raise ValueError("only a candle subscription has a period and a start time")


# The event records (decided 2026-11-23). A number is a float and a missing number is NaN,
# never zero and never text. A time is a UTC instant, or None when the broker gives none.
# A field is added when a package needs it.

type TradingStatus = Literal["active", "halted", "unknown"]


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    bid_time: datetime | None
    ask_time: datetime | None


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    price: float
    size: float
    time: datetime | None
    day_volume: float


@dataclass(frozen=True, slots=True)
class Summary:
    symbol: str
    day_open: float
    day_high: float
    day_low: float
    day_close: float
    prev_close: float
    open_interest: float


@dataclass(frozen=True, slots=True)
class Greeks:
    """The units are the broker's until WP-1.9 has compared them."""

    symbol: str
    time: datetime | None
    price: float
    iv: float
    delta: float
    gamma: float
    theta: float
    rho: float
    vega: float


@dataclass(frozen=True, slots=True)
class Profile:
    symbol: str
    description: str
    trading_status: TradingStatus
    halt_start: datetime | None
    halt_end: datetime | None
    high_52w: float
    low_52w: float


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    period: CandlePeriod
    time: datetime
    """The start of the bar. Always present: the broker module ignores a candle event
    without a time (decided 2026-11-24). On an event with ``removed`` it is not the start
    of a bar: the marker of a snapshot carries the instant where the history begins or was
    cut."""
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float
    iv: float
    open_interest: float
    snapshot_end: bool
    """The last event of a history snapshot, whether the history is complete or was cut at
    the broker's depth. The broker can state the marker again later; only the first one
    after a subscription ends the history."""
    removed: bool
    """Not a bar: there is no bar at this time. The marker that ends a snapshot carries
    both marks and no prices."""


type Record = Quote | Trade | Summary | Greeks | Profile | Candle
type RecordType = (
    type[Quote] | type[Trade] | type[Summary] | type[Greeks] | type[Profile] | type[Candle]
)

RECORDS: Mapping[EventType, RecordType] = {
    EventType.QUOTE: Quote,
    EventType.TRADE: Trade,
    EventType.SUMMARY: Summary,
    EventType.GREEKS: Greeks,
    EventType.PROFILE: Profile,
    EventType.CANDLE: Candle,
}
FIELDS: Mapping[EventType, tuple[str, ...]] = {
    event: tuple(f.name for f in fields(record)) for event, record in RECORDS.items()
}
"""The field names of each event's record, in the order of the columns of a batch."""


@dataclass(frozen=True, slots=True)
class EventBatch:
    """Events of one type as they arrived in one message. Plain data, no references.

    Columnar: ``columns`` holds one sequence per field of the event's record, in the order
    of ``FIELDS[event]``, all of one length. Row ``i`` of the batch is the ``i``-th value of
    every column.
    """

    seq: int
    """The stream's own counter over every item it emits, from 1."""
    event: EventType
    received_at: datetime
    """UTC. Taken when the message arrived, before it was decoded."""
    columns: tuple[Sequence[Any], ...]

    @property
    def count(self) -> int:
        return len(self.columns[0]) if self.columns else 0

    def column(self, name: str) -> Sequence[Any]:
        return self.columns[FIELDS[self.event].index(name)]

    def rows(self) -> Iterator[Record]:
        """Each event as its record. For tests and tools; a hot path reads the columns."""
        make = RECORDS[self.event]
        for values in zip(*self.columns, strict=True):
            yield make(*values)


type StreamPhase = Literal["idle", "connecting", "connected", "stalled", "disconnected", "closed"]
"""``idle``: not started. ``connecting``: opening the session. ``connected``: the session is
open and takes subscriptions; a session that has just opened holds none. ``stalled``:
connected, but no data for the configured time while data was expected; the session is
dropped next. ``disconnected``: no session; a new attempt follows after a wait. ``closed``:
closed by the application. "Resubscribing" is a state of the subscription manager, not of
the session."""


@dataclass(frozen=True, slots=True)
class StreamState:
    """The connection changed its phase (BRK-06)."""

    seq: int
    phase: StreamPhase
    at: datetime
    """UTC."""
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Gap:
    """Data was lost between two sessions of the stream (BRK-02).

    Emitted when a session is open again after a loss, before the phase ``connected`` and
    before any data of the new session. The new session holds no subscription: the data
    comes back as the subscription manager sends the granted set again.
    """

    seq: int
    last_seq: int
    """The number of the last batch before the loss; 0 if there was none."""
    since: datetime
    """UTC. When that batch arrived, or when the session was lost if there was none."""
    until: datetime
    """UTC. When the new session opened."""
    reason: str


@dataclass(frozen=True, slots=True)
class StreamNotice:
    """Something the broker's stream said that is not data: a refusal or an error."""

    seq: int
    at: datetime
    kind: Literal["rate", "refused", "error"]
    """``rate``: subscription changes came too fast, and the changes sent for this event
    type just before may not have counted. ``refused``: the broker refused a request.
    ``error``: any other error."""
    message: str
    event: EventType | None = None
    """The event type the notice belongs to, when the broker says it."""


type StreamItem = EventBatch | StreamState | Gap | StreamNotice
type StreamSink = Callable[[StreamItem], None]
"""Takes every item of a stream, in order. It must return at once and must not raise."""


@dataclass(frozen=True)
class StreamStatus:
    """The state of the session at this moment, for the status bar and the health route."""

    phase: StreamPhase
    since: datetime | None
    """UTC. When this phase began."""
    reason: str | None
    """Why the last session ended or stalled."""
    connected_at: datetime | None
    last_data_at: datetime | None
    reconnects: int
    """Sessions opened after the first one."""
    stalls: int
    last_gap: Gap | None
    subscribed: int
    """Pairs sent on the current connection, less the ones removed: the count the hard cap
    is held against."""
    candles: int
    """The candle subscriptions among them."""
    limit: int
    """The hard cap on ``subscribed``."""
    candle_limit: int
    aggregation_period: float | None
    """Seconds, as the server granted it."""


class Stream(Protocol):
    """One streaming session. It connects, keeps the connection alive, and reconnects by
    itself after a loss or a stall. It keeps no subscription set and never subscribes by
    itself: after a reconnect it is open and empty (decided 2026-11-23). Only the
    subscription manager calls ``subscribe`` and ``unsubscribe`` (SUB-02)."""

    def start(self) -> None:
        """Begin connecting. Returns at once; the sink sees the progress."""
        ...

    async def subscribe(self, subs: Collection[Sub]) -> None:
        """Send these subscriptions now, on the current connection. Returns when they were
        handed to the connection. Raises :class:`StreamRefused` when the session is not
        connected, or when the call would go beyond the hard cap; then nothing was sent."""
        ...

    async def unsubscribe(self, subs: Collection[Sub]) -> None:
        """Send these removals now, on the current connection. Raises
        :class:`StreamRefused` when the session is not connected."""
        ...

    def status(self) -> StreamStatus: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class DataSource:
    """What kind of data a broker serves (GEN-11)."""

    kind: Literal["live", "fake", "replay"]
    recorded_on: date | None = None
    """Replay only: the New York date of the recording."""

    @property
    def mark(self) -> str | None:
        """The permanent mark for the screens; None on the live broker."""
        if self.kind == "fake":
            return "SYNTHETIC DATA"
        if self.kind == "replay":
            return f"REPLAY {self.recorded_on.isoformat()}" if self.recorded_on else "REPLAY"
        return None


class BrokerError(Exception):
    """A broker call failed. The message is redacted and never holds a secret."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(BrokerError):
    """The broker refused the credentials, or the token renewal failed."""


class NotFound(BrokerError):
    """The broker does not know the symbol or the path (HTTP 404)."""


class RateLimited(BrokerError):
    """The broker kept refusing for the request rate (HTTP 429) after the retries."""

    def __init__(
        self, message: str, *, status: int | None = 429, retry_after: float | None = None
    ) -> None:
        super().__init__(message, status=status)
        self.retry_after = retry_after


class BrokerUnavailable(BrokerError):
    """The broker could not be reached, or kept answering with a server error."""


class BrokerProtocolError(BrokerError):
    """The broker answered with a status or a body that the client does not expect."""


class ForbiddenCall(BrokerError):
    """A request outside the allow-list. A programming error: it never reaches the network."""


type RefusalReason = Literal["not_connected", "limit", "candle_limit"]


class StreamRefused(BrokerError):
    """The session did not take a subscribe or unsubscribe call (decided 2026-11-23).

    ``not_connected``: there is no open session now, or it was lost during the sending; the
    next session starts empty, so nothing of the call counts. ``limit`` and
    ``candle_limit``: the call would go beyond the session's hard cap; nothing was sent.
    The cap is a backstop that the subscription manager never reaches in correct operation.
    """

    def __init__(self, message: str, *, reason: RefusalReason) -> None:
        super().__init__(message)
        self.reason: RefusalReason = reason


class Broker(Protocol):
    """What the application asks of a broker. Every method is a read."""

    async def quote_token(self) -> QuoteToken:
        """The token and address for the streaming session."""
        ...

    async def equity(self, symbol: str) -> JsonObject:
        """A stock, an ETF or an index."""
        ...

    async def equity_option(self, symbol: str) -> JsonObject:
        """One equity or index option by its OCC symbol, with exercise style, the expiry
        instant and the settlement type."""
        ...

    async def option_chain(self, underlying: str) -> JsonObject:
        """All option roots, expirations and strikes of an underlying, with the OCC and
        streamer symbols and the tick sizes."""
        ...

    async def future(self, symbol: str) -> JsonObject:
        """One futures contract, for example ``/ESZ6``."""
        ...

    async def future_option(self, symbol: str) -> JsonObject:
        """One futures option by its broker symbol."""
        ...

    async def futures_option_chain(self, root: str) -> JsonObject:
        """The futures of a product and their option expirations and strikes, for example
        ``/ES``."""
        ...

    async def market_metrics(self, symbols: Sequence[str]) -> list[JsonObject]:
        """IV index, rank and percentile, IV per expiration, historical volatility,
        earnings and dividends, liquidity and beta, one item per known symbol."""
        ...

    def rest_status(self) -> RestStatus:
        """The state of the REST connection, at this moment (BRK-09)."""
        ...

    def stream(self, sink: StreamSink) -> Stream:
        """The streaming session. Its items go to ``sink``. A broker has one session; a
        second call closes nothing and is an error."""
        ...

    def source(self) -> DataSource:
        """Live, fake or replay (GEN-11)."""
        ...

    def data_time(self) -> datetime:
        """UTC. The instant the broker's data belongs to: the wall clock on the live and
        the fake broker, the position in the recording on a replay. Time to expiry and the
        age of a quote are measured against it, never against the system clock."""
        ...

    async def aclose(self) -> None:
        """Release the connections."""
        ...
