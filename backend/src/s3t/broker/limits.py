"""Request pacing for the broker client (BRK-04).

A token bucket keeps the average request rate and the burst inside the configured limit
(``[broker]`` in the configuration). When the broker answers 429, :meth:`TokenBucket.pause_until`
holds every request, not only the refused one, until the retry time. :class:`Backoff` gives
the exponential waits for a retry without a stated retry time.

The clock and the sleep function are injected, so tests run on a fake clock and never sleep.
Proposed 2026-11-22 (WP-0.3): the defaults of :class:`Backoff`.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

type Clock = Callable[[], float]
"""Seconds on a monotonic clock."""
type Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """At most ``burst`` requests at once, refilled at ``rate`` per second. Waiters go in
    order of arrival."""

    def __init__(
        self,
        rate: float,
        burst: int,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be positive and burst at least 1")
        self.rate, self.burst = rate, burst
        self._clock, self._sleep = clock, sleep
        self._tokens = float(burst)
        self._stamp = clock()
        self._paused_until = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                if now < self._paused_until:
                    await self._sleep(self._paused_until - now)
                    continue
                self._tokens = min(self.burst, self._tokens + (now - self._stamp) * self.rate)
                self._stamp = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await self._sleep((1.0 - self._tokens) / self.rate)

    def pause_until(self, instant: float) -> None:
        """Hold every request until this instant of the clock. One request may go at the
        instant; the ones after it follow at the average rate, not in a burst."""
        if instant > self._paused_until:
            self._paused_until = instant
            self._tokens = 1.0
            self._stamp = max(self._stamp, instant)

    @property
    def paused_until(self) -> float:
        return self._paused_until


@dataclass(frozen=True)
class Backoff:
    """Exponential waits: ``first`` seconds, multiplied by ``factor`` per retry, at most
    ``cap``; with full jitter (a uniform draw below the bound). ``attempts`` counts every try,
    the first included."""

    first: float = 1.0
    factor: float = 2.0
    cap: float = 30.0
    attempts: int = 5
    jitter: bool = True
    max_retry_after: float = 60.0
    """A stated retry time longer than this is not waited for: the call fails with it."""

    def bound(self, retry: int) -> float:
        """The longest wait before retry number ``retry`` (0 is the first retry)."""
        return min(self.cap, self.first * self.factor**retry)

    def delay(self, retry: int, rng: random.Random) -> float:
        bound = self.bound(retry)
        return rng.uniform(0.0, bound) if self.jitter else bound


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header: a number of seconds or an HTTP date."""
    if value is None:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    return max(0.0, seconds) if seconds == seconds else None  # NaN is no answer
