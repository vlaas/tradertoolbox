"""The Tastytrade REST client: read-only market reads (BRK-01, BRK-04, BRK-07, BRK-08 market part,
BRK-09, GEN-01, GEN-02, SEC-05).

Source: `tt` tt_capture.py, class `TTClient`, version 1.6.0 (the `tt` directory is not a git
repository, so no commit can be named). Kept from it: the OAuth refresh-token grant posted
without an ``Authorization`` header; the JSON headers, with ``Accept-Version`` on the
production host only; symbols percent-encoded as one path segment; no secret in a log line.

Changed in the port (2026-11-22, WP-0.3):
- Requests are built only from the allow-list (``endpoints.py``). A request hook checks every
  outgoing request against it again, refuses another host, and refuses an ``Origin`` header,
  which makes Tastytrade answer 403 (note B, section 2).
- The access token is renewed on use when less than 60 s of its life is left. Only one renewal
  runs at a time. A 401 on a read leads to one renewal and one retry. A refused renewal is not
  retried: the client makes no further call until it is built again (a restart with a new
  grant). Decided 2026-11-22.
- A token bucket paces the requests. A 429 holds every request until ``Retry-After``, or for an
  exponential wait when the header is missing. 502, 503, 504 and network errors are retried
  the same way.
- The scope claim of the access token is read (the signature is not checked) and logged with
  the account masked. A grant that has more than read scope is refused like a refused renewal:
  its token is dropped unused (BRK-07, GEN-01; decided 2026-11-22).
- ``rest_status()`` gives the token state, the configured limit, the requests of the last
  minute and a hold after a 429, for the status bar (BRK-09).
- Bodies are never logged. `tt`'s ``--wire`` mode, the dry-run and the account reads are not
  ported.
"""

import asyncio
import base64
import json
import logging
import random
import re
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl

import httpx
from pydantic import SecretStr

from s3t import __version__
from s3t.broker.interface import (
    AuthError,
    BrokerError,
    BrokerProtocolError,
    BrokerUnavailable,
    ForbiddenCall,
    JsonObject,
    NotFound,
    QuoteToken,
    RateLimited,
    RestStatus,
)
from s3t.broker.limits import Backoff, Clock, Sleep, TokenBucket, parse_retry_after
from s3t.broker.tastytrade.endpoints import BY_NAME, Endpoint, match
from s3t.secrets import REDACTOR, BrokerSecrets, redact

API_URL = "https://api.tastyworks.com"
SANDBOX_URL = "https://api.cert.tastyworks.com"
API_VERSION = "20251101"  # as `tt` sends it
USER_AGENT = f"s3t/{__version__}"

RENEW_MARGIN_S = 60.0
DEFAULT_TOKEN_LIFETIME_S = 900.0  # measured, note B section 1
QUOTE_TOKEN_LIFETIME = timedelta(hours=24)  # measured, note B section 1
METRICS_BATCH = 50  # proposed; the broker's limit is not known
RETRY_STATUS = frozenset({502, 503, 504})
READ_SCOPES = frozenset({"read", "openid"})
TIMEOUT_S = 30.0

_ACCOUNT_SCOPE = re.compile(r"accounts:([^:\s]+):[a-z-]+")

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Token:
    value: str
    expires: float
    """On the client's monotonic clock."""


class TastytradeRest:
    """The Tastytrade implementation of :class:`s3t.broker.interface.Broker` (REST part)."""

    def __init__(
        self,
        secrets: BrokerSecrets,
        *,
        rate: float = 2.0,
        burst: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        backoff: Backoff | None = None,
        rng: random.Random | None = None,
        timeout: float = TIMEOUT_S,
    ) -> None:
        self._secrets = secrets
        REDACTOR.register(secrets.client_secret.get_secret_value())
        REDACTOR.register(secrets.refresh_token.get_secret_value())
        self.base_url = base_url or (SANDBOX_URL if secrets.sandbox else API_URL)
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self.base_url == API_URL:
            headers["Accept-Version"] = API_VERSION
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            event_hooks={"request": [self._check_request]},
        )
        self._bucket = TokenBucket(rate, burst, clock=clock, sleep=sleep)
        self._clock, self._sleep, self._now = clock, sleep, now
        self._backoff = backoff or Backoff()
        self._rng = rng or random.Random()
        self._token: _Token | None = None
        self._auth_lock = asyncio.Lock()
        self._scopes: tuple[str, ...] | None = None
        self._renewing = False
        self._refusal: str | None = None
        self._last_renewal: datetime | None = None
        self._sent: deque[float] = deque()
        self._rate_limited = 0
        self.renewals = 0
        """Access-token renewals so far."""
        self.quote_token_keys: tuple[str, ...] = ()
        """The key names of the last quote-token response, for the live check."""

    def __repr__(self) -> str:
        return f"TastytradeRest(base_url={self.base_url!r})"

    @property
    def granted_scopes(self) -> tuple[str, ...] | None:
        """The scopes of the current grant with the account masked, once a token is held."""
        return None if self._scopes is None else tuple(redact(s) for s in self._scopes)

    # ------------------------------------------------------------------ reads (Broker)

    async def quote_token(self) -> QuoteToken:
        data = await self._read("quote_token")
        self.quote_token_keys = tuple(sorted(data))
        token, url, level = data.get("token"), data.get("dxlink-url"), data.get("level", "")
        if not isinstance(token, str) or not isinstance(url, str):
            raise BrokerProtocolError("quote token: the response has no token or no address")
        REDACTOR.register(token, kind="quote")
        issued = self._now()
        expires = _parse_instant(data.get("expires-at")) or issued + QUOTE_TOKEN_LIFETIME
        return QuoteToken(url, SecretStr(token), str(level), issued, expires)

    async def equity(self, symbol: str) -> JsonObject:
        return await self._read("equity", symbol=symbol)

    async def equity_option(self, symbol: str) -> JsonObject:
        return await self._read("equity_option", symbol=symbol)

    async def option_chain(self, underlying: str) -> JsonObject:
        return await self._read("option_chain", symbol=underlying)

    async def future(self, symbol: str) -> JsonObject:
        return await self._read("future", symbol=symbol.removeprefix("/"))

    async def future_option(self, symbol: str) -> JsonObject:
        return await self._read("future_option", symbol=symbol)

    async def futures_option_chain(self, root: str) -> JsonObject:
        return await self._read("futures_option_chain", symbol=root.removeprefix("/"))

    async def market_metrics(self, symbols: Sequence[str]) -> list[JsonObject]:
        wanted = list(dict.fromkeys(symbols))
        items: list[JsonObject] = []
        for i in range(0, len(wanted), METRICS_BATCH):
            batch = ",".join(wanted[i : i + METRICS_BATCH])
            data = await self._read("market_metrics", query={"symbols": batch})
            found = data.get("items")
            if not isinstance(found, list) or not all(isinstance(x, dict) for x in found):
                raise BrokerProtocolError("market metrics: the response has no list of items")
            items.extend(found)
        return items

    def rest_status(self) -> RestStatus:
        mono, wall = self._clock(), self._now()

        def at(instant: float) -> datetime:
            return wall + timedelta(seconds=instant - mono)

        token = self._token
        if self._refusal is not None:
            auth = "refused"
        elif self._renewing:
            auth = "renewing"
        elif token is None:
            auth = "none"
        elif mono >= token.expires:
            auth = "lapsed"
        elif mono >= token.expires - RENEW_MARGIN_S:
            auth = "due"
        else:
            auth = "valid"
        self._forget_old_requests(mono)
        held = self._bucket.paused_until
        return RestStatus(
            auth=auth,
            token_expires_at=None if token is None else at(token.expires),
            renews_from=None if token is None else at(token.expires - RENEW_MARGIN_S),
            last_renewal_at=self._last_renewal,
            renewals=self.renewals,
            refusal=self._refusal,
            rate=self._bucket.rate,
            burst=self._bucket.burst,
            requests_last_minute=len(self._sent),
            held_until=at(held) if held > mono else None,
            rate_limited=self._rate_limited,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ requests

    def _forget_old_requests(self, now: float) -> None:
        while self._sent and now - self._sent[0] >= 60.0:
            self._sent.popleft()

    async def _read(
        self, name: str, *, query: Mapping[str, str] | None = None, **path: str
    ) -> dict[str, Any]:
        endpoint = BY_NAME[name]
        body = await self._send(endpoint, endpoint.path(**path), query=query)
        data = body.get("data")
        if not isinstance(data, dict):
            raise BrokerProtocolError(f"{endpoint.name}: the response has no data object")
        return data

    async def _send(
        self,
        endpoint: Endpoint,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Any = None,
        authed: bool = True,
    ) -> dict[str, Any]:
        label = redact(f"{endpoint.method} {path}")
        attempts = self._backoff.attempts
        renewed_after_401 = False
        last: BrokerError | None = None
        for attempt in range(attempts):
            headers: dict[str, str] = {}
            used: str | None = None
            if authed:
                used = await self._access_token()
                headers["Authorization"] = f"Bearer {used}"
            await self._bucket.acquire()
            started = self._clock()
            self._sent.append(started)
            self._forget_old_requests(started)
            LOG.debug("-> %s", label)
            try:
                r = await self._http.request(
                    endpoint.method, path, params=query, json=body, headers=headers
                )
            except ForbiddenCall:
                raise
            except httpx.TransportError as e:
                last = BrokerUnavailable(f"{label}: {type(e).__name__}")
                await self._retry_later(label, type(e).__name__, attempt)
                continue
            status = r.status_code
            LOG.debug(
                "<- %d %s in %.0f ms, %.1f KB",
                status,
                label,
                1000 * (self._clock() - started),
                len(r.content) / 1024,
            )
            if 200 <= status < 300:
                return _json_object(r, label)
            reason = f"{label}: HTTP {status}{_error_text(r)}"
            if not authed and 400 <= status < 500 and status != 429:
                raise AuthError(f"token renewal refused: {reason}", status=status)
            if status == 401 and not renewed_after_401:
                renewed_after_401 = True
                LOG.info("%s: HTTP 401; renewing the access token", label)
                await self._renew(stale=used)
                continue
            if status in (401, 403):
                raise AuthError(reason, status=status)
            if status == 404:
                raise NotFound(reason, status=status)
            if status == 429:
                self._rate_limited += 1
                stated = parse_retry_after(r.headers.get("Retry-After"), self._now())
                if stated is not None and stated > self._backoff.max_retry_after:
                    self._bucket.pause_until(self._clock() + stated)
                    raise RateLimited(f"{reason}; retry after {stated:.0f} s", retry_after=stated)
                wait = stated if stated is not None else self._backoff.delay(attempt, self._rng)
                self._bucket.pause_until(self._clock() + wait)
                last = RateLimited(f"{reason} on {attempt + 1} attempts", retry_after=wait)
                LOG.warning(
                    "%s: HTTP 429; every request waits %.1f s (attempt %d of %d)",
                    label,
                    wait,
                    attempt + 1,
                    attempts,
                )
                continue
            if status in RETRY_STATUS:
                last = BrokerUnavailable(f"{reason} on {attempt + 1} attempts", status=status)
                await self._retry_later(label, f"HTTP {status}", attempt)
                continue
            raise BrokerProtocolError(reason, status=status)
        assert last is not None
        raise last

    async def _retry_later(self, label: str, why: str, attempt: int) -> None:
        attempts = self._backoff.attempts
        if attempt + 1 >= attempts:
            LOG.warning("%s: %s; giving up after %d attempts", label, why, attempts)
            return
        wait = self._backoff.delay(attempt, self._rng)
        LOG.warning(
            "%s: %s; retry in %.1f s (attempt %d of %d)", label, why, wait, attempt + 1, attempts
        )
        await self._sleep(wait)

    async def _check_request(self, request: httpx.Request) -> None:
        """The last gate before the network: every request must be on the allow-list."""
        base = self._http.base_url
        if (request.url.scheme, request.url.host, request.url.port) != (
            base.scheme,
            base.host,
            base.port,
        ):
            raise ForbiddenCall(f"not the broker host: {request.url.host}")
        if "origin" in request.headers:
            raise ForbiddenCall("an Origin header makes the broker refuse the request")
        raw = request.url.raw_path.decode("ascii")
        path, _, query = raw.partition("?")
        match(request.method, path, (k for k, _ in parse_qsl(query, keep_blank_values=True)))

    # ------------------------------------------------------------------ token

    async def _access_token(self) -> str:
        if self._refusal is not None:
            raise AuthError(self._refusal)
        token = self._token
        if token is not None and self._clock() < token.expires - RENEW_MARGIN_S:
            return token.value
        return await self._renew(stale=None if token is None else token.value)

    async def _renew(self, stale: str | None) -> str:
        """Renew the access token unless another task renewed it while this one waited."""
        async with self._auth_lock:
            current = self._token
            if (
                current is not None
                and current.value != stale
                and self._clock() < current.expires - RENEW_MARGIN_S
            ):
                return current.value
            if self._refusal is not None:  # refused while this task waited for the lock
                raise AuthError(self._refusal)
            body = {
                "grant_type": "refresh_token",
                "client_secret": self._secrets.client_secret.get_secret_value(),
                "refresh_token": self._secrets.refresh_token.get_secret_value(),
            }
            self._renewing = True
            try:
                data = await self._send(BY_NAME["token"], "/oauth/token", body=body, authed=False)
            except AuthError as e:
                self._refuse(str(e))
                raise
            finally:
                self._renewing = False
            value, lifetime = data.get("access_token"), data.get("expires_in")
            if not isinstance(value, str) or not value:
                raise BrokerProtocolError("token renewal: the response has no access token")
            REDACTOR.register(value, kind="access")
            id_token = data.get("id_token")
            REDACTOR.register(id_token if isinstance(id_token, str) else None, kind="id")
            if not isinstance(lifetime, int | float) or lifetime <= 0:
                lifetime = DEFAULT_TOKEN_LIFETIME_S
            self._note_scopes(value)  # raises for a grant beyond read scope
            self._token = _Token(value, self._clock() + float(lifetime))
            self._last_renewal = self._now()
            self.renewals += 1
            LOG.info("access token renewed, valid for %.0f s", lifetime)
            return value

    def _refuse(self, reason: str) -> None:
        """No further call until the client is built again (a restart with a new grant)."""
        self._refusal = redact(reason)
        self._token = None
        LOG.error("%s; no broker call is made until a restart with a new grant", self._refusal)

    def _note_scopes(self, token: str) -> None:
        scopes = jwt_scopes(token)
        if scopes is None or scopes == self._scopes:
            return
        self._scopes = scopes
        for scope in scopes:
            m = _ACCOUNT_SCOPE.fullmatch(scope)
            if m:
                REDACTOR.register_account(m.group(1))
        LOG.info("broker grant scopes: %s", redact(" ".join(scopes)))
        extra = [s for s in scopes if s not in READ_SCOPES and not _ACCOUNT_SCOPE.fullmatch(s)]
        if extra:
            reason = (
                f"the grant has more than read scope ({' '.join(extra)}); use a read-only grant"
            )
            self._refuse(reason)
            raise AuthError(self._refusal or reason)


def jwt_scopes(token: str) -> tuple[str, ...] | None:
    """The ``scope`` claim of a JWT, without checking its signature; None if not a JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return None
    scope = claims.get("scope") if isinstance(claims, dict) else None
    if isinstance(scope, str):
        return tuple(scope.split())
    if isinstance(scope, list) and all(isinstance(s, str) for s in scope):
        return tuple(scope)
    return None


def _json_object(r: httpx.Response, label: str) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        raise BrokerProtocolError(f"{label}: the response is not JSON") from None
    if not isinstance(body, dict):
        raise BrokerProtocolError(f"{label}: the response is not a JSON object")
    return body


def _error_text(r: httpx.Response) -> str:
    """The broker's error code and message, redacted and short; empty if there is none."""
    try:
        body = r.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    parts: list[str] = []
    if isinstance(error, dict):
        parts = [str(error[k]) for k in ("code", "message") if error.get(k)]
    elif isinstance(error, str):
        parts = [error]
        if isinstance(body.get("error_description"), str):
            parts.append(body["error_description"])
    text = redact(": ".join(parts))[:200]
    return f" ({text})" if text else ""


def _parse_instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC)
