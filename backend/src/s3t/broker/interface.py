"""The broker interface (GEN-09).

Everything the application asks of a broker is a method of :class:`Broker`, so another broker
can be judged against this list. The list grows with the packages that need it:

- WP-0.3, here: the REST reads of market data. Instruments, option chains (equity and
  futures), market metrics and the streaming token. All are read-only (GEN-01, BRK-08).
- WP-0.4, here: the stream session. Subscribe, unsubscribe, events as plain batches, the
  connection state. Candles come over the stream. Also ``source()`` and ``now()``, which
  say what kind of data the broker serves and which instant it belongs to (GEN-11).
- R5: the account reads (positions, balances, transactions).

Nothing in the interface places, changes or cancels an order or changes account state, and
nothing may be added that does (GEN-01).

Instruments, chains and metrics are returned as the broker's JSON documents (read-only
mappings) until the package that first uses each one gives it a type. That is WP-0.5 for
instruments and chains (the instrument model), and WP-1.1 and WP-1.2 for the metrics.
Decided 2026-11-22, with a rule: once a document is typed, this interface returns only the
typed record and the broker's JSON stays inside ``s3t.broker.tastytrade``. Until then,
nothing outside ``s3t.broker`` reads broker keys, except the package typing the document.
"""

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
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
    """UTC. Tastytrade does not state it in the response. 24 hours after issue, as measured
    in the probe (note B, section 1), unless the response carries an expiry."""


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
    """The stream events the application asks for. Proposed 2026-11-23 (WP-0.4).

    The names are dxFeed's, because the fields of a batch travel as received until WP-0.5
    types them (requirements, section 3).
    """

    QUOTE = "Quote"
    TRADE = "Trade"
    SUMMARY = "Summary"
    GREEKS = "Greeks"
    PROFILE = "Profile"
    CANDLE = "Candle"


@dataclass(frozen=True, slots=True)
class Sub:
    """One subscription: a stream symbol and an event type.

    ``symbol`` is the broker's stream symbol (the ``streamer-symbol`` of an instrument). For
    a candle it carries the period, as the broker writes it. Outside the broker module it is
    an opaque key.
    """

    symbol: str
    event: EventType
    from_time: datetime | None = None
    """Candles only, UTC: history from this instant on. It is part of the identity, so a
    changed start is a new subscription."""


@dataclass(frozen=True, slots=True)
class EventBatch:
    """Events of one type as they arrived in one message. Plain data, no references.

    ``values`` is flat: ``count`` events times ``len(fields)`` values, in the order of
    ``fields``. A number the broker could not give arrives as the string ``"NaN"``.
    """

    seq: int
    """The stream's own counter over every item it emits, from 1."""
    event: EventType
    fields: tuple[str, ...]
    """The field names in the order the server set (broker keys; typed at WP-0.5)."""
    values: Sequence[Any]
    received_at: datetime
    """UTC. Taken when the message arrived, before it was decoded."""

    @property
    def count(self) -> int:
        return len(self.values) // len(self.fields)

    def rows(self) -> Iterator[dict[str, Any]]:
        """Each event as a mapping. For tests and tools; a hot path reads ``values``."""
        n = len(self.fields)
        for i in range(0, len(self.values), n):
            yield dict(zip(self.fields, self.values[i : i + n], strict=True))


type StreamPhase = Literal[
    "idle", "connecting", "resubscribing", "connected", "stalled", "disconnected", "closed"
]
"""``idle``: not started. ``connecting``: opening the session. ``resubscribing``: the session
is open and the held subscriptions are being sent. ``connected``: all of them are sent.
``stalled``: connected, but no data for the configured time while data was expected; the
session is dropped next. ``disconnected``: no session; a new attempt follows after a wait.
``closed``: closed by the application."""


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
    """Data was lost between two items of the stream (BRK-02).

    Emitted when the subscriptions are back after a reconnect. Nothing arrived between the
    item ``last_seq`` and this item.
    """

    seq: int
    last_seq: int
    """The last batch before the gap; 0 if there was none."""
    since: datetime
    """UTC. When that batch arrived, or when the session was lost if there was none."""
    until: datetime
    """UTC. When the subscriptions were sent again."""
    reason: str


@dataclass(frozen=True, slots=True)
class StreamNotice:
    """Something the broker's stream said that is not data: a refusal or an error."""

    seq: int
    at: datetime
    kind: Literal["rate", "refused", "error"]
    """``rate``: subscription changes came too fast. The stream slows down and sends the
    changes again. ``refused``: the broker refused a request. ``error``: any other error."""
    message: str


type StreamItem = EventBatch | StreamState | Gap | StreamNotice
type StreamSink = Callable[[StreamItem], None]
"""Takes every item of a stream, in order. It must return at once and must not raise."""


@dataclass(frozen=True)
class StreamStatus:
    """The state of the stream at this moment, for the status bar and the health route."""

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
    held: int
    """Subscriptions the stream was told to hold."""
    pending: int
    """Changes not sent yet."""
    changes_last_minute: int
    change_limit: int
    """Changes the stream allows itself in any 60 s."""
    rate_errors: int
    aggregation_period: float | None
    """Seconds, as the server granted it."""


class Stream(Protocol):
    """One streaming session. It reconnects by itself and sends the held subscriptions
    again (BRK-02). Only the subscription manager calls ``subscribe`` and ``unsubscribe``
    (SUB-02)."""

    def start(self) -> None:
        """Begin connecting. Returns at once; the sink sees the progress."""
        ...

    def subscribe(self, subs: Collection[Sub]) -> None:
        """Hold these subscriptions from now on. Returns at once; they are sent as the pace
        allows, and again after every reconnect."""
        ...

    def unsubscribe(self, subs: Collection[Sub]) -> None:
        """Stop holding these subscriptions."""
        ...

    async def synced(self) -> None:
        """Wait until every change asked so far has been sent on an open session."""
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

    def now(self) -> datetime:
        """UTC. The instant the broker's data belongs to: the wall clock on the live and
        the fake broker, the recorded time on a replay. Time to expiry and the age of a
        quote are measured against it."""
        ...

    async def aclose(self) -> None:
        """Release the connections."""
        ...
