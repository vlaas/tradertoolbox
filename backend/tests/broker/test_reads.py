"""The market reads of the client against the fake broker (BRK-08, market part)."""

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from s3t.broker.fake.clock import FakeClock
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.interface import BrokerProtocolError, ForbiddenCall, NotFound
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
    assert (spy["is-etf"], spy["is-index"]) == (True, False)
    assert spx["is-index"] is True
    assert spy["streamer-symbol"] == "SPY"


async def test_option_chain_and_one_contract(client: TastytradeRest) -> None:
    chain = await client.option_chain("SPX")
    roots = {item["root-symbol"]: item for item in chain["items"]}
    assert set(roots) == {"SPXW", "SPX"}
    assert {e["settlement-type"] for e in roots["SPX"]["expirations"]} == {"AM"}
    strike = roots["SPXW"]["expirations"][3]["strikes"][100]
    contract = await client.equity_option(strike["put"])
    assert contract["streamer-symbol"] == strike["put-streamer-symbol"]
    assert contract["exercise-style"] == "European"


async def test_futures(client: TastytradeRest, fake: FakeTastytrade) -> None:
    chain = await client.futures_option_chain("/ES")
    assert [f["symbol"] for f in chain["futures"]] == ["/ESZ6", "/ESH7", "/ESM7"]
    assert (await client.future("/ESZ6")) == (await client.future("ESZ6"))
    strike = chain["option-chains"][0]["expirations"][0]["strikes"][10]
    option = await client.future_option(strike["call"])
    assert option["streamer-symbol"] == strike["call-streamer-symbol"]
    paths = [r.raw_path for r in fake.requests]
    assert "/futures-option-chains/ES/nested" in paths
    assert "/instruments/futures/ESZ6" in paths


async def test_market_metrics_are_batched(client: TastytradeRest, fake: FakeTastytrade) -> None:
    unknown = [f"X{i:03d}" for i in range(2 * METRICS_BATCH)]
    items = await client.market_metrics(["SPY", "AAPL", "SPY", *unknown, "SPX"])
    assert [i["symbol"] for i in items] == ["SPY", "AAPL", "SPX"]
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
        assert (await client.equity("SPY"))["symbol"] == "SPY"
    finally:
        await client.aclose()
