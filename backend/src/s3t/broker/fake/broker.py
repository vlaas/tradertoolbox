"""The fake broker as one object: the real Tastytrade client on the fake REST transport and
the fake stream, over one synthetic market (implementation plan 3.2 and 3.3).

The client code that runs is the live one. Only the transports are fake, so what the tests
and the app see on the fake broker is what the client would do on the live one. The data
source says ``fake``, and the app marks every screen "SYNTHETIC DATA" (GEN-11).

On the wall clock the market's as-of instant is the start of the process. A test gives a
clock, and the market and the stream follow it.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.fake.stream import FakeDxLink
from s3t.broker.interface import DataSource
from s3t.broker.limits import Clock, Sleep
from s3t.broker.tastytrade.broker import Tastytrade
from s3t.secrets import BrokerSecrets

FAKE_CLIENT_SECRET = "fake-client-secret"
FAKE_REFRESH_TOKEN = "fake-refresh-token"


class FakeBroker(Tastytrade):
    """:attr:`rest` and :attr:`dxlink` are the two fake servers, with their scenarios."""

    def __init__(
        self,
        *,
        seed: int = 1,
        market: SyntheticMarket | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        **options: Any,
    ) -> None:
        self.market = market or SyntheticMarket(now(), seed=seed)
        self.rest = FakeTastytrade(
            self.market,
            client_secret=FAKE_CLIENT_SECRET,
            refresh_token=FAKE_REFRESH_TOKEN,
            clock=clock,
        )
        self.dxlink = FakeDxLink(self.market, now=now, rest=self.rest)
        super().__init__(
            BrokerSecrets(SecretStr(FAKE_CLIENT_SECRET), SecretStr(FAKE_REFRESH_TOKEN)),
            transport=self.rest.transport,
            base_url="https://api.fake.invalid",
            stream_connect=self.dxlink.connect,
            source=DataSource("fake"),
            clock=clock,
            sleep=sleep,
            now=now,
            **options,
        )
