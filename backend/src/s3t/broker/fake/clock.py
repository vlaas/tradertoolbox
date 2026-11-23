"""Manual clocks for tests and for replay.

:class:`FakeClock` moves when someone sleeps on it. It is exact for one sleeper at a time,
which is the REST client. :class:`VirtualClock` moves only when the test advances it and wakes
sleepers in time order. It is for code with several timers at once: the stream session with
its keepalive, its watchdog and its reconnect waits (WP-0.4).
"""

import asyncio
import heapq
import itertools
from datetime import UTC, datetime, timedelta


class FakeClock:
    """A monotonic clock, a sleep that advances it, and the wall time that goes with it.

    ``sleep`` yields to the event loop once, so other tasks run, but it does not wait. Each
    sleep moves the clock by its own amount, so two tasks that sleep at once move it twice.
    That is exact for the client, whose waits are serial (one limiter, one renewal lock).
    """

    def __init__(self, wall_start: datetime | None = None, start: float = 1000.0) -> None:
        self.now = start
        self._start = start
        self._wall_start = wall_start or datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
        self.slept: list[float] = []
        """Every sleep, in order."""

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        self.slept.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def wall(self) -> datetime:
        return self._wall_start + timedelta(seconds=self.now - self._start)


class VirtualClock:
    """Time that moves only in :meth:`advance`. A sleeper wakes when the clock reaches its
    instant; sleepers due at the same instant wake in the order they went to sleep."""

    SETTLE_ROUNDS = 60
    """Turns of the event loop given to the other tasks after each wake-up. A message
    through an in-memory connection needs two or three."""

    def __init__(self, wall_start: datetime | None = None, start: float = 1000.0) -> None:
        self.now = start
        self._start = start
        self._wall_start = wall_start or datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> datetime:
        return self._wall_start + timedelta(seconds=self.now - self._start)

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        woken: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self.now + seconds, next(self._order), woken))
        await woken

    async def settle(self, rounds: int | None = None) -> None:
        """Let the other tasks run until they wait again."""
        for _ in range(rounds or self.SETTLE_ROUNDS):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        """Move the clock forward, waking every sleeper that falls due on the way."""
        target = self.now + seconds
        await self.settle()
        while self._sleepers and self._sleepers[0][0] <= target:
            due, _, woken = heapq.heappop(self._sleepers)
            if woken.done():  # its task was cancelled
                continue
            self.now = max(self.now, due)
            woken.set_result(None)
            await self.settle()
        self.now = target
        await self.settle()

    @property
    def sleepers(self) -> int:
        return sum(1 for _, _, woken in self._sleepers if not woken.done())
