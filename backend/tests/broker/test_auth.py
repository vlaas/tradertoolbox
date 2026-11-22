"""Token renewal against the fake broker (BRK-01, BRK-07)."""

import asyncio
import logging

import pytest
from pydantic import SecretStr

from s3t.broker.fake.clock import FakeClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.interface import AuthError
from s3t.broker.tastytrade.rest import API_URL, SANDBOX_URL, TastytradeRest, jwt_scopes
from s3t.secrets import BrokerSecrets

from .conftest import ACCOUNT, CLIENT_SECRET, REFRESH_TOKEN, MakeClient

pytestmark = pytest.mark.anyio


async def test_one_token_serves_many_reads(client: TastytradeRest, fake: FakeTastytrade) -> None:
    for _ in range(5):
        await client.equity("SPY")
    assert fake.issued == 1
    assert client.renewals == 1


async def test_token_request_has_no_authorization_and_reads_have(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    await client.equity("SPY")
    token, read = fake.requests
    assert (token.method, token.path) == ("POST", "/oauth/token")
    assert "authorization" not in token.header_names
    assert "authorization" in read.header_names


async def test_no_request_has_an_origin_header(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    await client.equity("SPY")
    await client.quote_token()
    await client.market_metrics(["SPY"])
    assert all("origin" not in r.header_names for r in fake.requests)


async def test_renewed_before_expiry(
    client: TastytradeRest, fake: FakeTastytrade, clock: FakeClock
) -> None:
    await client.equity("SPY")
    clock.advance(900 - 61)  # 61 s of the token's life left
    await client.equity("SPY")
    assert fake.issued == 1
    clock.advance(2)  # 59 s left: renewed before the read
    await client.equity("SPY")
    assert fake.issued == 2
    assert all(r.status != 401 for r in fake.requests)


async def test_concurrent_reads_share_one_renewal(
    make_client: MakeClient, fake: FakeTastytrade
) -> None:
    client = make_client(burst=10, rate=10.0)
    await asyncio.gather(*(client.equity("SPY") for _ in range(8)))
    assert fake.issued == 1


async def test_token_expiry_during_a_session_is_recovered(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    await client.equity("SPY")
    fake.expire_tokens()
    doc = await client.equity("AAPL")
    assert doc["symbol"] == "AAPL"
    assert fake.issued == 2
    assert [r.status for r in fake.requests] == [200, 200, 401, 200, 200]


async def test_concurrent_401s_renew_once(make_client: MakeClient, fake: FakeTastytrade) -> None:
    client = make_client(burst=10, rate=10.0)
    await client.equity("SPY")
    fake.expire_tokens()
    await asyncio.gather(*(client.equity("SPY") for _ in range(4)))
    assert fake.issued == 2


async def test_refused_renewal_is_an_auth_error_without_retries(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    fake.refuse_renewal()
    with pytest.raises(AuthError) as info:
        await client.equity("SPY")
    assert info.value.status == 401
    assert "invalid_grant" in str(info.value)
    assert [r.path for r in fake.requests] == ["/oauth/token"]
    # Not retried later either: no further request until the client is built again.
    fake.refuse_renewal(False)
    with pytest.raises(AuthError, match="invalid_grant"):
        await client.equity("SPY")
    assert len(fake.requests) == 1
    assert client.rest_status().auth == "refused"


async def test_a_401_after_a_fresh_renewal_is_an_auth_error(
    market: SyntheticMarket, clock: FakeClock, make_client: MakeClient
) -> None:
    # The fake issues tokens that it already treats as expired.
    fake = FakeTastytrade(
        market,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
        clock=clock.monotonic,
        token_lifetime=0,
    )
    client = make_client(transport=fake.transport)
    with pytest.raises(AuthError):
        await client.equity("SPY")
    assert [r.status for r in fake.requests] == [200, 401, 200, 401]


async def test_read_scope_is_logged_with_the_account_masked(
    client: TastytradeRest, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="s3t.broker"):
        await client.equity("SPY")
    assert client.granted_scopes == ("read", "openid", "accounts:<acct>:owner")
    assert "broker grant scopes: read openid accounts:<acct>:owner" in caplog.text
    assert ACCOUNT not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_more_than_read_scope_is_refused(
    market: SyntheticMarket,
    clock: FakeClock,
    make_client: MakeClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeTastytrade(
        market,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
        account=ACCOUNT,
        scope="read trade openid",
        clock=clock.monotonic,
    )
    client = make_client(transport=fake.transport)
    with caplog.at_level(logging.INFO, logger="s3t.broker"):
        with pytest.raises(AuthError, match=r"more than read scope \(trade\)"):
            await client.equity("SPY")
        with pytest.raises(AuthError, match="more than read scope"):
            await client.market_metrics(["SPY"])
    # The token was dropped unused: no read reached the broker, and no second renewal.
    assert [r.path for r in fake.requests] == ["/oauth/token"]
    status = client.rest_status()
    assert (status.auth, status.token_expires_at) == ("refused", None)
    assert status.refusal is not None and "read-only grant" in status.refusal
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and ACCOUNT not in caplog.text


def test_jwt_scopes() -> None:
    assert jwt_scopes("not-a-jwt") is None
    assert jwt_scopes("a.b.c") is None
    assert jwt_scopes("eyJhIjoxfQ.eyJzY29wZSI6InJlYWQgb3BlbmlkIn0.sig") == ("read", "openid")


async def test_production_and_sandbox_headers(
    make_client: MakeClient, fake: FakeTastytrade
) -> None:
    production = make_client()
    assert production.base_url == API_URL
    assert production._http.headers["accept-version"]
    assert production._http.headers["user-agent"].startswith("s3t/")
    sandbox_secrets = BrokerSecrets(SecretStr(CLIENT_SECRET), SecretStr(REFRESH_TOKEN), True)
    sandbox = make_client(secrets=sandbox_secrets)
    assert sandbox.base_url == SANDBOX_URL
    await sandbox.equity("SPY")
    assert all("accept-version" not in r.header_names for r in fake.requests)


def test_client_implements_the_interface() -> None:
    from s3t.broker.interface import Broker

    def typed(client: TastytradeRest) -> Broker:  # checked by pyright
        return client

    assert typed
