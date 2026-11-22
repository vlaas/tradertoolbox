"""Fixtures for the broker tests: the synthetic market, the fake broker, a client on a fake
clock. The secrets and the account number are canaries: the tests check that they never
leave the backend (implementation plan 3.6)."""

import random
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from s3t.broker.fake.clock import FakeClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.tastytrade.rest import TastytradeRest
from s3t.secrets import BrokerSecrets

ASOF = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)  # a Friday, 10:00 in New York
CLIENT_SECRET = "canary-client-secret-5d1e9b77"
REFRESH_TOKEN = "canary-refresh-token-8c2f4a19"
ACCOUNT = "5WZ99173"

type MakeClient = Callable[..., TastytradeRest]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def market() -> SyntheticMarket:
    return SyntheticMarket(ASOF, seed=1)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(ASOF)


@pytest.fixture
def secrets() -> BrokerSecrets:
    return BrokerSecrets(SecretStr(CLIENT_SECRET), SecretStr(REFRESH_TOKEN))


@pytest.fixture
def fake(market: SyntheticMarket, clock: FakeClock) -> FakeTastytrade:
    return FakeTastytrade(
        market,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
        account=ACCOUNT,
        clock=clock.monotonic,
    )


@pytest.fixture
async def make_client(
    fake: FakeTastytrade, clock: FakeClock, secrets: BrokerSecrets
) -> AsyncIterator[MakeClient]:
    made: list[TastytradeRest] = []

    def make(**overrides: Any) -> TastytradeRest:
        own_secrets = overrides.pop("secrets", secrets)
        options: dict[str, Any] = {
            "transport": fake.transport,
            "clock": clock.monotonic,
            "sleep": clock.sleep,
            "now": clock.wall,
            "rng": random.Random(7),
            **overrides,
        }
        client = TastytradeRest(own_secrets, **options)
        made.append(client)
        return client

    yield make
    for client in made:
        await client.aclose()


@pytest.fixture
def client(make_client: MakeClient) -> TastytradeRest:
    return make_client()
