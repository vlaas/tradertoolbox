"""Fixtures for the feed tests: the in-process feed on the real session, the fake stream and
the virtual clock of the broker tests."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from broker.conftest import ASOF, StreamRig, anyio_backend, market, vclock  # noqa: F401
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.config import SubscriptionsConfig
from s3t.subs.feed import FeedItem, FillState, Lost, Reduction
from s3t.subs.local import LocalFeed


class FeedRig(StreamRig):
    """A feed on the fake stream. ``seen`` is what a reader of everything got; the helpers
    of :class:`StreamRig` (batches, rows, phases, gaps, notices) read the same items."""

    def __init__(
        self,
        market: SyntheticMarket,  # noqa: F811
        clock: VirtualClock,
        config: SubscriptionsConfig | None = None,
        **options: Any,
    ) -> None:
        super().__init__(market, clock, subscriptions=config)
        self.config = config or SubscriptionsConfig()
        self.feed = LocalFeed(
            self.broker, self.config, clock=clock.monotonic, sleep=clock.sleep, **options
        )
        self.seen: list[FeedItem] = []
        self._collector = asyncio.create_task(self._collect())

    async def _collect(self) -> None:
        async for item in self.feed.reader("everything", maxsize=100_000):
            self.seen.append(item)
            if not isinstance(item, FillState | Reduction | Lost):
                self.items.append(item)

    async def up(self) -> None:
        """Start the feed and wait for the session to be connected."""
        self.feed.start()
        for _ in range(20):
            if self.feed.status().stream.phase == "connected":
                await self.clock.settle()
                return
            await self.clock.settle()
        raise AssertionError(f"the session is {self.feed.status().stream.phase}")

    async def settled(self) -> None:
        """Every change is sent. Fails instead of waiting for a clock that nobody moves."""
        await self.clock.advance(0.06)  # the moment for which changes are gathered
        await asyncio.wait_for(self.feed.settled(), timeout=2.0)
        await self.clock.settle()

    def fills(self) -> list[FillState]:
        return [i for i in self.seen if isinstance(i, FillState)]

    def reductions(self) -> list[Reduction]:
        return [i for i in self.seen if isinstance(i, Reduction)]

    def adds(self) -> list[list[str]]:
        """The symbols of each subscription message with additions, in order."""
        return [
            [a["symbol"] for a in m["add"]] for m in self.sent("FEED_SUBSCRIPTION") if "add" in m
        ]

    async def aclose(self) -> None:
        await self.feed.aclose()
        self._collector.cancel()
        await super().aclose()


@pytest.fixture
async def rig(market: SyntheticMarket, vclock: VirtualClock) -> AsyncIterator[FeedRig]:  # noqa: F811
    made = FeedRig(market, vclock)
    yield made
    await made.aclose()


__all__ = ["ASOF", "FeedRig"]
