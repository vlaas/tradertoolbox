"""Fixtures for the broker tests: the synthetic market, the fake broker, a client on a fake
clock. The secrets and the account number are canaries: the tests check that they never
leave the backend (implementation plan 3.6).

The stream tests use :class:`StreamRig`: the fake REST server, the fake DXLink server and a
session, all on one virtual clock.
"""

import asyncio
import random
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from s3t.broker.fake.clock import FakeClock, VirtualClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.fake.stream import FakeDxLink
from s3t.broker.interface import (
    EventBatch,
    EventType,
    Gap,
    Record,
    StreamItem,
    StreamNotice,
    StreamState,
)
from s3t.broker.limits import Backoff
from s3t.broker.tastytrade.broker import Tastytrade
from s3t.broker.tastytrade.dxlink import DxLinkSession
from s3t.config import SubscriptionsConfig
from s3t.secrets import BrokerSecrets

ASOF = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)  # a Friday, 10:00 in New York
CLIENT_SECRET = "canary-client-secret-5d1e9b77"
REFRESH_TOKEN = "canary-refresh-token-8c2f4a19"
ACCOUNT = "5WZ99173"

type MakeClient = Callable[..., Tastytrade]


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
    made: list[Tastytrade] = []

    def make(**overrides: Any) -> Tastytrade:
        own_secrets = overrides.pop("secrets", secrets)
        options: dict[str, Any] = {
            "transport": fake.transport,
            "clock": clock.monotonic,
            "sleep": clock.sleep,
            "now": clock.wall,
            "rng": random.Random(7),
            **overrides,
        }
        client = Tastytrade(own_secrets, **options)
        made.append(client)
        return client

    yield make
    for client in made:
        await client.aclose()


@pytest.fixture
def client(make_client: MakeClient) -> Tastytrade:
    return make_client()


class StreamRig:
    """A session against the fake stream on a virtual clock. ``items`` is what the sink got."""

    def __init__(
        self,
        market: SyntheticMarket,
        clock: VirtualClock,
        subscriptions: SubscriptionsConfig | None = None,
    ) -> None:
        self.clock = clock
        self.rest = FakeTastytrade(
            market,
            client_secret=CLIENT_SECRET,
            refresh_token=REFRESH_TOKEN,
            account=ACCOUNT,
            clock=clock.monotonic,
        )
        self.dxlink = FakeDxLink(market, now=clock.wall, rest=self.rest)
        self.broker = Tastytrade(
            BrokerSecrets(SecretStr(CLIENT_SECRET), SecretStr(REFRESH_TOKEN)),
            transport=self.rest.transport,
            stream_connect=self.dxlink.connect,
            subscriptions=subscriptions,
            clock=clock.monotonic,
            sleep=clock.sleep,
            now=clock.wall,
            rng=random.Random(7),
        )
        self.items: list[StreamItem] = []
        self.sessions: list[DxLinkSession] = []
        self._ticker: Any = None

    def session(self, **overrides: Any) -> DxLinkSession:
        """A session with waits that do not vary: no jitter in the reconnect waits."""
        clock = self.clock
        options: dict[str, Any] = {
            "connect": self.dxlink.connect,
            "clock": clock.monotonic,
            "sleep": clock.sleep,
            "now": clock.wall,
            "backoff": Backoff(jitter=False),
            **overrides,
        }
        token_source = options.pop("token_source", self.broker.quote_token)
        sink = options.pop("sink", self.items.append)
        session = DxLinkSession(token_source, sink, **options)
        self.sessions.append(session)
        return session

    def tick_every_second(self) -> None:
        self._ticker = asyncio.create_task(self.dxlink.run(self.clock.sleep))

    async def open(self, **overrides: Any) -> DxLinkSession:
        """A started session that is connected: authorized, with its six channels open."""
        session = self.session(**overrides)
        session.start()
        await self.until_connected(session)
        return session

    async def until_connected(self, session: DxLinkSession | None = None) -> None:
        """Fails instead of waiting for a clock that nobody moves."""
        session = session or self.sessions[-1]
        for _ in range(20):
            if session.status().phase == "connected":
                return
            await self.clock.settle()
        raise AssertionError(f"the session is {session.status().phase}, not connected")

    def batches(self, event: EventType | None = None) -> list[EventBatch]:
        found = [i for i in self.items if isinstance(i, EventBatch)]
        return found if event is None else [b for b in found if b.event is event]

    def rows(self, event: EventType, symbol: str | None = None) -> list[Any]:
        """The records of one event type, as the sink got them."""
        rows: list[Record] = [row for b in self.batches(event) for row in b.rows()]
        return rows if symbol is None else [r for r in rows if r.symbol == symbol]

    def phases(self) -> list[str]:
        return [i.phase for i in self.items if isinstance(i, StreamState)]

    def gaps(self) -> list[Gap]:
        return [i for i in self.items if isinstance(i, Gap)]

    def notices(self) -> list[StreamNotice]:
        return [i for i in self.items if isinstance(i, StreamNotice)]

    def sent(self, kind: str) -> list[dict[str, Any]]:
        """The messages of one type that the fake server received."""
        return [m for m in self.dxlink.received if m["type"] == kind]

    async def aclose(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
        for session in self.sessions:
            await session.aclose()
        await self.broker.aclose()


@pytest.fixture
def vclock() -> VirtualClock:
    return VirtualClock(ASOF)


@pytest.fixture
async def rig(market: SyntheticMarket, vclock: VirtualClock) -> AsyncIterator[StreamRig]:
    made = StreamRig(market, vclock)
    yield made
    await made.aclose()
