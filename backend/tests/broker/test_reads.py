"""The market reads of the client against the fake broker (BRK-08, market part)."""

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from s3t.broker.fake.clock import FakeClock
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.interface import BrokerProtocolError, DividendFacts, ForbiddenCall, NotFound
from s3t.broker.tastytrade.broker import from_config
from s3t.broker.tastytrade.rest import METRICS_BATCH, TastytradeRest
from s3t.config import load_config

from .conftest import CLIENT_SECRET, REFRESH_TOKEN, MakeClient

pytestmark = pytest.mark.anyio


async def test_quote_token(client: TastytradeRest, clock: FakeClock) -> None:
    token = await client.quote_token()
    assert token.url.startswith("wss://") and token.level == "api"
    assert token.token.get_secret_value().startswith("fake-dxlink-token-")
    assert token.issued_at == clock.wall()
    assert token.expires_at - token.issued_at == timedelta(hours=24)


async def test_equities_and_indexes(client: TastytradeRest) -> None:
    spy, spx = await client.equity("SPY"), await client.equity("SPX")
    assert (spy.kind, spx.kind) == ("etf", "index")
    assert spy.stream_symbol == "SPY"


async def test_option_chain_and_one_contract(client: TastytradeRest) -> None:
    chain = await client.option_chain("SPX")
    assert set(chain.roots()) == {"SPXW", "SPXO", "SPX"}
    assert {e.settlement for e in chain.of_root("SPX")} == {"am"}
    listed = chain.of_root("SPXW")[3].contract(100, "put")
    assert listed is not None and listed.exercise is None and listed.expires_at is None
    contract = await client.equity_option(listed.symbol)
    assert contract.exercise == "european" and contract.expires_at is not None
    # Except for what only the contract states, the chain says the same.
    assert (
        replace(
            listed,
            exercise=contract.exercise,
            expires_at=contract.expires_at,
            stops_trading_at=contract.stops_trading_at,
        )
        == contract
    )


async def test_futures(client: TastytradeRest, fake: FakeTastytrade) -> None:
    chain = await client.futures_option_chain("/ES")
    assert [f.symbol for f in chain.futures] == ["/ESZ6", "/ESH7", "/ESM7"]  # in date order
    assert (await client.future("/ESZ6")) == (await client.future("ESZ6"))
    listed = chain.expirations[0].contract(10, "call")
    assert listed is not None
    assert await client.future_option(listed.symbol) == listed
    paths = [r.raw_path for r in fake.requests]
    assert "/futures-option-chains/ES/nested" in paths
    assert "/instruments/futures/ESZ6" in paths


async def test_market_metrics_are_batched(client: TastytradeRest, fake: FakeTastytrade) -> None:
    unknown = [f"X{i:03d}" for i in range(2 * METRICS_BATCH)]
    items = await client.market_metrics(["SPY", "AAPL", "SPY", *unknown, "SPX"])
    assert [i.symbol for i in items] == ["SPY", "AAPL", "SPX"]
    spy, aapl, spx = items
    assert spy.dividend == DividendFacts(date(2026, 9, 21), 1.75, date(2026, 10, 5))
    assert aapl.dividend is not None and aapl.dividend.amount == 0.26
    assert spx.dividend is None  # an index: dates of 2020 and an amount of zero
    batches = [dict(r.query)["symbols"].split(",") for r in fake.requests if r.query]
    assert [len(b) for b in batches] == [METRICS_BATCH, METRICS_BATCH, 3]


async def test_unknown_symbol_is_not_found(client: TastytradeRest) -> None:
    with pytest.raises(NotFound):
        await client.option_chain("NOSUCH")
    with pytest.raises(NotFound):
        await client.equity_option("SPY   261120C00000001")


async def test_a_bad_symbol_never_reaches_the_broker(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    with pytest.raises(ForbiddenCall):
        await client.equity("SPY/../../accounts?x")
    assert fake.requests == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"nodata": 1}),
        httpx.Response(200, json={"data": [1, 2]}),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(302, headers={"Location": "/elsewhere"}),
    ],
)
async def test_an_unexpected_body_is_a_protocol_error(
    make_client: MakeClient, fake: FakeTastytrade, response: httpx.Response
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return fake.handle(request) if request.url.path == "/oauth/token" else response

    client = make_client(transport=httpx.MockTransport(handler))
    with pytest.raises(BrokerProtocolError):
        await client.equity("SPY")


async def test_metrics_without_items_is_a_protocol_error(
    make_client: MakeClient, fake: FakeTastytrade
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return fake.handle(request)
        return httpx.Response(200, content=json.dumps({"data": {"items": "none"}}))

    client = make_client(transport=httpx.MockTransport(handler))
    with pytest.raises(BrokerProtocolError):
        await client.market_metrics(["SPY"])


async def test_from_config(tmp_path: Path, fake: FakeTastytrade) -> None:
    env = tmp_path / "broker.env"
    env.write_text(f"TASTY_CLIENT_SECRET={CLIENT_SECRET}\nTASTY_REFRESH_TOKEN={REFRESH_TOKEN}\n")
    env.chmod(0o600)
    config_file = tmp_path / "s3t.toml"
    config_file.write_text(
        '[server]\nlisten_address = "127.0.0.1"\nport = 8740\n'
        '[paths]\nenv_file = "broker.env"\ndata_dir = "data"\nstate_file = "state.sqlite"\n'
        "[broker]\nrest_rate = 1.5\nrest_burst = 3\n"
    )
    client = from_config(load_config(config_file), transport=fake.transport)
    try:
        assert client._bucket.rate == 1.5 and client._bucket.burst == 3
        assert (await client.equity("SPY")).symbol == "SPY"
    finally:
        await client.aclose()
