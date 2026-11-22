"""A manual clock for tests and for replay: time moves only when someone sleeps on it."""

import asyncio
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
