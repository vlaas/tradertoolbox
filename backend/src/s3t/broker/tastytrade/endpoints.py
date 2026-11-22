"""The allow-list of Tastytrade REST calls (GEN-01; implementation plan 3.6).

This table is the only way the client builds a request, and the client's request hook checks
every outgoing request against it once more. Every entry is a read (GET), except the OAuth
token renewal, which is a POST that changes nothing in the account.

There is no entry for orders, the order dry-run, the margin dry-run, transfers or any write.
``tests/broker/test_allow_list.py`` holds a frozen copy of the table and fails when an entry
is added, changed or matches a state-changing path. A new entry is a decision for the
review, not a side effect.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from s3t.broker.interface import ForbiddenCall
from s3t.secrets import redact

type Method = Literal["GET", "POST"]

_PARAM = re.compile(r"\{([a-z_]+)\}")
# Symbols as Tastytrade writes them: letters, digits, space (OCC), '.', '/', ':' (streamer and
# futures symbols), '_' and '-'. Anything else is refused before it reaches a path.
_SYMBOL = re.compile(r"[A-Za-z0-9 ./:_-]{1,64}")


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: Method
    template: str
    """The path; each ``{name}`` is one percent-encoded path segment."""
    query: frozenset[str] = frozenset()
    """The query parameters it takes."""

    @property
    def params(self) -> tuple[str, ...]:
        return tuple(_PARAM.findall(self.template))

    @property
    def pattern(self) -> re.Pattern[str]:
        """The raw (percent-encoded) path this endpoint may send."""
        parts = _PARAM.split(self.template)
        # split() alternates literal text and parameter names.
        regex = "".join(re.escape(p) if i % 2 == 0 else "[^/?#]+" for i, p in enumerate(parts))
        return re.compile(regex)

    def path(self, **values: str) -> str:
        if set(values) != set(self.params):
            raise ForbiddenCall(f"{self.name}: takes {self.params}, given {tuple(values)}")
        for name, value in values.items():
            if not _SYMBOL.fullmatch(value):
                raise ForbiddenCall(f"{self.name}: {name} is not a symbol")

        def encode(m: re.Match[str]) -> str:
            return quote(values[m.group(1)], safe="")

        return _PARAM.sub(encode, self.template)


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("token", "POST", "/oauth/token"),
    Endpoint("quote_token", "GET", "/api-quote-tokens"),
    Endpoint("equity", "GET", "/instruments/equities/{symbol}"),
    Endpoint("equity_option", "GET", "/instruments/equity-options/{symbol}"),
    Endpoint("option_chain", "GET", "/option-chains/{symbol}/nested"),
    Endpoint("future", "GET", "/instruments/futures/{symbol}"),
    Endpoint("future_option", "GET", "/instruments/future-options/{symbol}"),
    Endpoint("futures_option_chain", "GET", "/futures-option-chains/{symbol}/nested"),
    Endpoint("market_metrics", "GET", "/market-metrics", frozenset({"symbols"})),
)

BY_NAME: dict[str, Endpoint] = {e.name: e for e in ENDPOINTS}


def match(method: str, raw_path: str, query_keys: Iterable[str] = ()) -> Endpoint:
    """The endpoint that allows this request, or :class:`ForbiddenCall`.

    ``raw_path`` is the path as sent, percent-encoded, without the query.
    """
    keys = set(query_keys)
    for endpoint in ENDPOINTS:
        if (
            endpoint.method == method
            and endpoint.pattern.fullmatch(raw_path)
            and keys <= endpoint.query
        ):
            return endpoint
    raise ForbiddenCall(redact(f"not on the allow-list: {method} {raw_path}"))
