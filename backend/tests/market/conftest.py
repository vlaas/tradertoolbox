"""Fixtures for the market tests: the market state on the in-process feed, the real session,
the fake stream and the virtual clock of the feed tests."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from broker.conftest import ASOF, anyio_backend, clock, fake, market, vclock  # noqa: F401
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import EventBatch, EventType, Sub
from s3t.config import SubscriptionsConfig
from s3t.market.state import Changes, Flush, MarketState
from s3t.subs.feed import Priority
from subs.conftest import FeedRig

PERIOD = 0.5
"""The aggregation period that the fake server grants."""


class MarketRig(FeedRig):
    def __init__(
        self,
        market: SyntheticMarket,  # noqa: F811
        clock: VirtualClock,  # noqa: F811
        config: SubscriptionsConfig | None = None,
        **options: Any,
    ) -> None:
        super().__init__(market, clock, config, **options)
        self.state = MarketState(
            self.feed, now=self.broker.data_time, period=2.0, sleep=clock.sleep
        )
        self.demand = self.feed.consumer("test", Priority.VIEWS)
        self._takers: list[asyncio.Task[None]] = []

    async def up(self) -> None:
        """The session is up and the flushes come at the period it was granted. The state saw
        every item since the feed was started: its reader is as old as itself."""
        await super().up()
        self.state.start()
        await self.clock.settle()

    async def want(self, *subs: Sub) -> None:
        """State the demand and wait until it was sent and its first values are here."""
        self.demand.set(subs)
        await self.settled()
        await self.clock.settle()

    async def publish(self, *batches: EventBatch) -> None:
        """Put batches of the test's own into the feed, as if the session had sent them."""
        for batch in batches:
            self.feed._on_item(batch)
        await self.clock.settle()

    def collect(self, taker: Changes) -> list[Flush]:
        """Take every flush as it comes, into the list returned."""
        taken: list[Flush] = []

        async def take() -> None:
            async for flush in taker:
                taken.append(flush)

        self._takers.append(asyncio.create_task(take()))
        return taken

    async def aclose(self) -> None:
        await self.state.aclose()
        await asyncio.gather(*self._takers)
        await super().aclose()


def quotes(rig: MarketRig, **prices: tuple[float, float]) -> EventBatch:
    """A batch of quotes, received now: symbol to bid and ask."""
    n = len(prices)
    columns = (
        list(prices),
        [bid for bid, _ in prices.values()],
        [ask for _, ask in prices.values()],
        [1.0] * n,
        [1.0] * n,
        [None] * n,
        [None] * n,
    )
    return EventBatch(0, EventType.QUOTE, rig.clock.wall(), columns)


@pytest.fixture
async def rig(market: SyntheticMarket, vclock: VirtualClock) -> AsyncIterator[MarketRig]:  # noqa: F811
    made = MarketRig(market, vclock)
    yield made
    await made.aclose()


__all__ = ["ASOF", "PERIOD", "MarketRig", "quotes"]
