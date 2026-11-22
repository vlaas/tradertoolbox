"""The allow-list of broker calls (GEN-01; implementation plan 3.6).

The frozen copy below is the reviewed table. Adding, removing or changing an entry makes
the first test fail on purpose: a new broker call is a decision for review. The account
reads of R5 will change ``test_no_entry_touches_the_account`` deliberately.
"""

import pytest

from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.interface import ForbiddenCall
from s3t.broker.tastytrade.endpoints import BY_NAME, ENDPOINTS, Endpoint, match
from s3t.broker.tastytrade.rest import TastytradeRest

from .conftest import ACCOUNT

FROZEN = (
    ("token", "POST", "/oauth/token", ()),
    ("quote_token", "GET", "/api-quote-tokens", ()),
    ("equity", "GET", "/instruments/equities/{symbol}", ()),
    ("equity_option", "GET", "/instruments/equity-options/{symbol}", ()),
    ("option_chain", "GET", "/option-chains/{symbol}/nested", ()),
    ("future", "GET", "/instruments/futures/{symbol}", ()),
    ("future_option", "GET", "/instruments/future-options/{symbol}", ()),
    ("futures_option_chain", "GET", "/futures-option-chains/{symbol}/nested", ()),
    ("market_metrics", "GET", "/market-metrics", ("symbols",)),
)

# Fragments of Tastytrade paths that place or change orders, move money or change settings.
STATE_CHANGING = (
    "order",
    "dry-run",
    "margin",
    "transfer",
    "withdraw",
    "deposit",
    "ach-",
    "watchlist",
    "password",
    "session",
    "customers",
    "/accounts",
)


def test_table_is_the_reviewed_one() -> None:
    table = tuple((e.name, e.method, e.template, tuple(sorted(e.query))) for e in ENDPOINTS)
    assert table == FROZEN


def test_only_the_token_renewal_is_not_a_get() -> None:
    assert [e for e in ENDPOINTS if e.method != "GET"] == [BY_NAME["token"]]


def test_no_entry_touches_the_account() -> None:
    for endpoint in ENDPOINTS:
        path = endpoint.template.lower()
        assert not [f for f in STATE_CHANGING if f in path], endpoint


@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        ("POST", f"/accounts/{ACCOUNT}/orders", ()),
        ("POST", f"/accounts/{ACCOUNT}/orders/dry-run", ()),
        ("POST", f"/margin/accounts/{ACCOUNT}/dry-run", ()),
        ("PUT", f"/accounts/{ACCOUNT}/orders/1", ()),
        ("PATCH", f"/accounts/{ACCOUNT}/orders/1", ()),
        ("DELETE", f"/accounts/{ACCOUNT}/orders/1", ()),
        ("GET", f"/accounts/{ACCOUNT}/positions", ()),
        ("POST", "/instruments/equities/SPY", ()),
        ("DELETE", "/oauth/token", ()),
        ("GET", "/oauth/token", ()),
        ("GET", "/instruments/equities/SPY/extra", ()),
        ("GET", "/instruments/equities/", ()),
        ("GET", "/market-metrics", ("symbols", "account")),
        ("POST", "/watchlists", ()),
    ],
)
def test_match_refuses(method: str, path: str, query: tuple[str, ...]) -> None:
    with pytest.raises(ForbiddenCall) as info:
        match(method, path, query)
    assert ACCOUNT not in str(info.value)


def test_a_parameter_stays_one_segment() -> None:
    path = BY_NAME["equity"].path(symbol="../accounts")
    assert path == "/instruments/equities/..%2Faccounts"
    assert match("GET", path) is BY_NAME["equity"]  # a read of an unknown symbol, no more
    assert BY_NAME["equity_option"].path(symbol="SPY   261120C00588500") == (
        "/instruments/equity-options/SPY%20%20%20261120C00588500"
    )


@pytest.mark.parametrize("symbol", ["SPY?x=1", "SPY#", "SP%59", "", "A" * 65, "SPY\n"])
def test_a_parameter_must_look_like_a_symbol(symbol: str) -> None:
    with pytest.raises(ForbiddenCall):
        BY_NAME["equity"].path(symbol=symbol)


def test_parameters_must_be_complete() -> None:
    with pytest.raises(ForbiddenCall):
        BY_NAME["equity"].path()
    with pytest.raises(ForbiddenCall):
        BY_NAME["quote_token"].path(symbol="SPY")


@pytest.mark.anyio
async def test_the_client_refuses_before_the_network(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    http = client._http
    with pytest.raises(ForbiddenCall) as info:
        await http.post(f"/accounts/{ACCOUNT}/orders", json={})
    assert ACCOUNT not in str(info.value)
    with pytest.raises(ForbiddenCall):
        await http.get("https://elsewhere.invalid/instruments/equities/SPY")
    with pytest.raises(ForbiddenCall):
        await http.get("/instruments/equities/SPY", headers={"Origin": "http://localhost:5173"})
    order = Endpoint("order", "POST", "/accounts/{symbol}/orders")
    with pytest.raises(ForbiddenCall):
        await client._send(order, order.path(symbol=ACCOUNT), body={})
    # Only the token renewal that _send made first reached the fake.
    assert [r.path for r in fake.requests] == ["/oauth/token"]


def test_the_client_has_only_reads() -> None:
    # Public methods and properties of the class; a new one is a decision for review.
    public = {n for n in dir(TastytradeRest) if not n.startswith("_")}
    assert public == {
        "aclose",
        "equity",
        "equity_option",
        "future",
        "future_option",
        "futures_option_chain",
        "granted_scopes",
        "market_metrics",
        "option_chain",
        "quote_token",
        "rest_status",
    }
