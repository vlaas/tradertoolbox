"""The fake Tastytrade REST server over the synthetic market (implementation plan 3.3).

It is an httpx transport (``FakeTastytrade.transport``), so the real client runs against it
in the same process, without ports. It answers like Tastytrade where the probes saw how
Tastytrade answers. The OAuth refresh grant returns a JWT-shaped access token with a scope
claim, valid for 900 s. The reads return ``{"data": ...}``. Errors look like
``{"error": {"code", "message"}}``. A request with an ``Origin`` header gets 403 (note B,
section 2).

Scenarios, for the tests and for trying the app (implementation plan 3.3):

- :meth:`expire_tokens`: every issued access token is invalid from now on, so the next read
  gets 401, as when a token expires during a session.
- :meth:`throttle`: the next N requests get 429, with a ``Retry-After`` or without one.
- :meth:`fail`: the next N requests get a server error (503 by default).
- :meth:`disconnect`: the next N requests fail with a connection error.
- :meth:`refuse_renewal`: the refresh grant is refused (401 ``invalid_grant``).
- ``rate_limit``: a sliding-window request limit that answers 429 with the retry time.

Every request is recorded with its method, its path, its query and the names of its headers
(never their values) in :attr:`FakeTastytrade.requests`.

Source: the REST half of `tt` tests/fake_tt.py (version 1.6.0 of `tt`, which is not a git
repository), moved onto the synthetic market and given the scenarios above (2026-11-22,
WP-0.3).
"""

import base64
import json
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote

import httpx

from s3t.broker.fake.market import DXLINK_URL, SyntheticMarket

type Clock = Callable[[], float]

TOKEN_LIFETIME_S = 900


@dataclass(frozen=True)
class Recorded:
    method: str
    path: str
    """Decoded."""
    raw_path: str
    """As sent, percent-encoded, without the query."""
    query: tuple[tuple[str, str], ...]
    header_names: frozenset[str]
    """Lower case. Values are not recorded."""
    at: float
    status: int = 0


@dataclass
class _Scenario:
    throttle: deque[float | str | None] = field(default_factory=deque)
    fail: deque[int] = field(default_factory=deque)
    disconnect: int = 0
    refuse_renewal: bool = False


def _b64(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _error(status: int, code: str, message: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": code, "message": message}}, headers=headers
    )


class FakeTastytrade:
    def __init__(
        self,
        market: SyntheticMarket,
        *,
        client_secret: str,
        refresh_token: str,
        account: str = "5WT00001",
        scope: str = "read openid",
        clock: Clock = time.monotonic,
        token_lifetime: int = TOKEN_LIFETIME_S,
        rate_limit: tuple[int, float] | None = None,
        dxlink_url: str = DXLINK_URL,
    ) -> None:
        self.market = market
        self._client_secret, self._refresh_token = client_secret, refresh_token
        self.scope = f"{scope} accounts:{account}:owner"
        self._clock = clock
        self.token_lifetime = token_lifetime
        self.rate_limit = rate_limit
        self.dxlink_url = dxlink_url
        self._tokens: dict[str, float] = {}
        self._window: deque[float] = deque()
        self._scenario = _Scenario()
        self.issued = 0
        """Access tokens issued so far."""
        self.issued_tokens: list[str] = []
        """Every access and id token issued, for the tests that look for leaks."""
        self.quote_tokens: list[str] = []
        self.requests: list[Recorded] = []
        self.transport = httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------ scenarios

    def expire_tokens(self) -> None:
        self._tokens.clear()

    def throttle(self, n: int = 1, retry_after: float | str | None = None) -> None:
        self._scenario.throttle.extend([retry_after] * n)

    def fail(self, n: int = 1, status: int = 503) -> None:
        self._scenario.fail.extend([status] * n)

    def disconnect(self, n: int = 1) -> None:
        self._scenario.disconnect += n

    def refuse_renewal(self, refuse: bool = True) -> None:
        self._scenario.refuse_renewal = refuse

    # ------------------------------------------------------------------ server

    def handle(self, request: httpx.Request) -> httpx.Response:
        raw = request.url.raw_path.decode("ascii")
        raw_path, _, query = raw.partition("?")
        record = Recorded(
            method=request.method,
            path=unquote(raw_path),
            raw_path=raw_path,
            query=tuple(parse_qsl(query, keep_blank_values=True)),
            header_names=frozenset(k.lower() for k in request.headers),
            at=self._clock(),
        )
        if self._scenario.disconnect:
            self._scenario.disconnect -= 1
            self.requests.append(record)
            raise httpx.ConnectError("fake broker: connection refused", request=request)
        response = self._respond(request, record)
        self.requests.append(Recorded(**{**record.__dict__, "status": response.status_code}))
        return response

    def _respond(self, request: httpx.Request, record: Recorded) -> httpx.Response:
        if "origin" in record.header_names:
            return _error(403, "forbidden", "Forbidden")
        limited = self._rate_limited(record.at)
        if limited is not None:
            return limited
        if self._scenario.throttle:
            retry_after = self._scenario.throttle.popleft()
            headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
            return _error(429, "too_many_requests", "Too many requests", **headers)
        if self._scenario.fail:
            return _error(self._scenario.fail.popleft(), "unavailable", "Service unavailable")
        if record.path == "/oauth/token":
            return self._token(request, record)
        if not self._authorised(request):
            return _error(401, "unauthorized", "Token is invalid or expired")
        if request.method != "GET":
            return _error(404, "not_found", "Not found")
        return self._read(record)

    def _rate_limited(self, now: float) -> httpx.Response | None:
        if self.rate_limit is None:
            return None
        n, window = self.rate_limit
        while self._window and now - self._window[0] >= window:
            self._window.popleft()
        if len(self._window) >= n:
            wait = max(1, math.ceil(window - (now - self._window[0])))
            return _error(
                429, "too_many_requests", "Too many requests", **{"Retry-After": str(wait)}
            )
        self._window.append(now)
        return None

    def _token(self, request: httpx.Request, record: Recorded) -> httpx.Response:
        if request.method != "POST" or "authorization" in record.header_names:
            return httpx.Response(400, json={"error": "invalid_request"})
        try:
            body = json.loads(request.content)
        except ValueError:
            return httpx.Response(400, json={"error": "invalid_request"})
        if (
            self._scenario.refuse_renewal
            or body.get("grant_type") != "refresh_token"
            or body.get("client_secret") != self._client_secret
            or body.get("refresh_token") != self._refresh_token
        ):
            return httpx.Response(
                401, json={"error": "invalid_grant", "error_description": "Grant is invalid"}
            )
        self.issued += 1
        now = self._clock()
        claims = {"scope": self.scope, "iat": int(now), "exp": int(now) + self.token_lifetime}
        header = _b64({"alg": "HS256", "typ": "JWT"})
        access = f"{header}.{_b64({**claims, 'jti': f'a{self.issued}'})}.fakesignature{self.issued}"
        id_token = f"{header}.{_b64({'sub': 'fake', 'jti': f'i{self.issued}'})}.fakeid{self.issued}"
        self._tokens[access] = now + self.token_lifetime
        self.issued_tokens += [access, id_token]
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": self.token_lifetime,
                "id_token": id_token,
            },
        )

    def _authorised(self, request: httpx.Request) -> bool:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return False
        expires = self._tokens.get(header[len("Bearer ") :])
        return expires is not None and self._clock() < expires

    def _read(self, record: Recorded) -> httpx.Response:
        # Segments of the raw path, decoded one by one: a symbol may hold an encoded '/'.
        parts = [unquote(p) for p in record.raw_path.strip("/").split("/")]
        doc: dict[str, Any] | None = None
        if record.path == "/api-quote-tokens":
            token = f"fake-dxlink-token-{len(self.quote_tokens) + 1:04d}"
            self.quote_tokens.append(token)
            doc = {"token": token, "dxlink-url": self.dxlink_url, "level": "api"}
        elif record.path == "/market-metrics":
            symbols = dict(record.query).get("symbols", "")
            items = [self.market.market_metrics(s) for s in symbols.split(",") if s]
            doc = {"items": [i for i in items if i is not None]}
        elif len(parts) == 3 and parts[:2] == ["instruments", "equities"]:
            doc = self.market.equity(parts[2])
        elif len(parts) == 3 and parts[:2] == ["instruments", "equity-options"]:
            doc = self.market.equity_option(parts[2])
        elif len(parts) == 3 and parts[:2] == ["instruments", "futures"]:
            doc = self.market.future(parts[2])
        elif len(parts) == 3 and parts[:2] == ["instruments", "future-options"]:
            doc = self.market.future_option(parts[2])
        elif len(parts) == 3 and parts[0] == "option-chains" and parts[2] == "nested":
            doc = self.market.option_chain(parts[1])
        elif len(parts) == 3 and parts[0] == "futures-option-chains" and parts[2] == "nested":
            doc = self.market.futures_option_chain(parts[1])
        if doc is None:
            return _error(404, "not_found", f"Not found: {record.path}")
        return httpx.Response(200, json={"data": doc, "context": record.path})
