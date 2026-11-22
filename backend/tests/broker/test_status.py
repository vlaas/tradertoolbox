"""The REST state for the status bar (BRK-09)."""

from datetime import timedelta

import httpx
import pytest

from s3t.broker.fake.clock import FakeClock
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.interface import RateLimited
from s3t.broker.tastytrade.rest import TastytradeRest

from .conftest import MakeClient

pytestmark = pytest.mark.anyio


async def test_before_any_call(client: TastytradeRest) -> None:
    status = client.rest_status()
    assert status.auth == "none"
    assert (status.token_expires_at, status.renews_from, status.last_renewal_at) == (None,) * 3
    assert (status.rate, status.burst) == (2.0, 4)
    assert (status.requests_last_minute, status.renewals, status.rate_limited) == (0, 0, 0)
    assert status.held_until is None and status.refusal is None


async def test_token_life_cycle(client: TastytradeRest, clock: FakeClock) -> None:
    await client.equity("SPY")
    renewed = clock.wall()
    status = client.rest_status()
    assert status.auth == "valid"
    assert status.last_renewal_at == renewed
    assert status.token_expires_at == renewed + timedelta(seconds=900)
    assert status.renews_from == renewed + timedelta(seconds=840)
    assert (status.renewals, status.requests_last_minute) == (1, 2)

    clock.advance(845)  # inside the renewal margin: the next call renews first
    assert client.rest_status().auth == "due"
    assert client.rest_status().requests_last_minute == 0  # older than a minute
    clock.advance(60)  # expired while nothing was called
    assert client.rest_status().auth == "lapsed"
    await client.equity("SPY")
    assert (client.rest_status().auth, client.rest_status().renewals) == ("valid", 2)


async def test_renewing(make_client: MakeClient, fake: FakeTastytrade) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            seen.append(client.rest_status().auth)
        return fake.handle(request)

    client = make_client(transport=httpx.MockTransport(handler))
    await client.equity("SPY")
    assert seen == ["renewing"]
    assert client.rest_status().auth == "valid"


async def test_a_hold_after_429(
    client: TastytradeRest, fake: FakeTastytrade, clock: FakeClock
) -> None:
    await client.equity("SPY")
    fake.throttle(1, retry_after=600)  # longer than the client waits: fails, holds
    with pytest.raises(RateLimited):
        await client.equity("SPY")
    status = client.rest_status()
    assert status.rate_limited == 1
    assert status.held_until == clock.wall() + timedelta(seconds=600)
    clock.advance(601)
    assert client.rest_status().held_until is None


async def test_configured_rate(make_client: MakeClient) -> None:
    status = make_client(rate=1.5, burst=3).rest_status()
    assert (status.rate, status.burst) == (1.5, 3)
