"""Fixtures for the tests of the surface service: the whole backend on the fake broker and a virtual
clock, with the fits run in place of the process pool, so that a test is the same on every run."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from math import exp
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from api.conftest import ASOF, ApiRig, make_config, running
from s3t.analysis.surface import SurfaceFit, SurfaceService, Watch, WatchedStatus
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.fake.market import UNDERLYINGS
from s3t.broker.fake.ticks import RATE
from s3t.pricing import tau_cal
from s3t.subs.feed import ConsumerUse
from system.conftest import anyio_backend  # noqa: F401

NY = ZoneInfo("America/New_York")

QUIET = "[stream]\nstall_after = 3600.0\n"
"""The fake stream may fall silent in a test without the session being given up for it."""
REFRESH = 5.0
SMALL = QUIET + f"[surface]\nmax_dte = 30\nwing_strikes = 8\nrefresh_s = {REFRESH}\n"
"""Fewer pairs than the defaults ask for and a short timer, where a test is not about them."""


class InPlace:
    """Runs a task where it is called, as the pool does in a worker, and keeps the arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.error: Exception | None = None
        """Raised in place of the next results."""

    async def run(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return fn(*args, **kwargs)


@dataclass
class SurfaceRig:
    api: ApiRig
    pool: InPlace

    @property
    def service(self) -> SurfaceService:
        return self.api.backend.surface

    def status(self, underlying: str) -> WatchedStatus:
        return next(w for w in self.service.status().watched if w.underlying == underlying)

    def use(self) -> ConsumerUse | None:
        """The use of the service's consumer of the feed; None when it has none."""
        consumers = self.api.backend.feed.status().consumers
        return next((c for c in consumers if c.name == "surface"), None)

    async def until(self, done: Callable[[], bool], seconds: float = 90) -> None:
        """Let time pass until something holds."""
        for _ in range(int(seconds * 2)):
            if done():
                return
            await self.api.pass_time(0.5)
        raise AssertionError("it did not come in time")

    async def filled(self, watch: Watch) -> SurfaceFit:
        """The latest fit once the set of the underlying has filled."""
        name = watch.underlying
        await self.until(lambda: watch.latest is not None and not self.status(name).filling)
        assert watch.latest is not None
        return watch.latest

    async def on_demand(self, watch: Watch) -> SurfaceFit:
        """A fit on demand. It needs no time: the pool runs it in place."""
        import asyncio

        task = asyncio.create_task(watch.fit())
        await self.api.clock.settle()
        assert task.done(), "the fit on demand did not run at once"
        return task.result()

    def market_forward(self, symbol: str, root: str, key: str, at: datetime) -> float:
        """The forward the synthetic market prices an expiration on, at an instant."""
        fake = self.api.fake
        day = datetime.fromisoformat(key.split(":")[1]).date()
        listed = next(r for r in UNDERLYINGS[symbol].roots if r.symbol == root)
        spot = fake.dxlink.ticks.price(symbol, at)
        assert spot is not None
        tau = tau_cal(at, fake.market.settles_at(listed, day))
        pv = fake.market.pv_dividends(symbol, at.astimezone(NY).date(), day, RATE)
        return (spot - pv) * exp(RATE * tau)


@asynccontextmanager
async def surfaces(
    tmp_path: Path, more: str = SMALL, *, start: datetime = ASOF
) -> AsyncIterator[SurfaceRig]:
    """The backend on the fake broker, its session up. ``more`` goes on after the section of the
    broker in the configuration."""
    pool = InPlace()
    config = make_config(tmp_path, 'source = "fake"\n' + more)
    async with running(config, VirtualClock(start), fits=pool) as api:
        await api.up()
        yield SurfaceRig(api, pool)
