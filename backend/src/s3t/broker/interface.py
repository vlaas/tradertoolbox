"""The broker interface (GEN-09).

Everything the application asks of a broker is a method of :class:`Broker`, so another broker
can be judged against this list. The list grows with the packages that need it:

- WP-0.3, here: the REST reads of market data. Instruments, option chains (equity and
  futures), market metrics and the streaming token. All are read-only (GEN-01, BRK-08).
- WP-0.4: the stream session (DXLink): subscribe, unsubscribe, events, connection state.
  Candles come over the stream.
- R5: the account reads (positions, balances, transactions).

Nothing in the interface places, changes or cancels an order or changes account state, and
nothing may be added that does (GEN-01).

Instruments, chains and metrics are returned as the broker's JSON documents (read-only
mappings) until the package that first uses each one gives it a type. That is WP-0.5 for
instruments and chains (the instrument model), and WP-1.1 and WP-1.2 for the metrics.
Proposed 2026-11-22 (WP-0.3).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

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

    async def aclose(self) -> None:
        """Release the connections."""
        ...
