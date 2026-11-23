"""Request pacing for the broker client (BRK-04).

A token bucket keeps the average request rate and the burst inside the configured limit
(``[broker]`` in the configuration). When the broker answers 429, :meth:`TokenBucket.pause_until`
holds every request, not only the refused one, until the retry time. :class:`Backoff` gives
the exponential waits for a retry without a stated retry time.

The clock and the sleep function are injected, so tests run on a fake clock and never sleep.
Decided 2026-11-22: the defaults of :class:`Backoff` and of the bucket (configuration).

:class:`ChangePacer` does the same for subscription changes on the stream (WP-0.4).
"""

import asyncio
import random
import time
from collections import deque
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


class ChangePacer:
    """Paces subscription changes on the stream (SUB-02).

    Tastytrade publishes a limit of 10,000 subscription changes a minute. The pacer keeps the
    changes under ``window_cap`` in any ``window`` seconds, and under ``rate`` a second with a
    burst of ``burst``. When the server still says the rate is too high, every change waits
    and the rate is halved. It returns to the configured rate after ``recover_after`` seconds
    without such an error.

    Source: `tt` tt_capture.py 1.6.0, ``DXLinkFeed._pace`` and ``_on_rate_error``. Changed in
    the port (2026-11-23, WP-0.4): the clock and the sleep are injected, and the rate
    recovers, because this session lives for weeks and `tt`'s for minutes.
    """

    def __init__(
        self,
        *,
        rate: float = 150.0,
        burst: int = 100,
        window_cap: int = 8000,
        window: float = 60.0,
        pause: float = 5.0,
        min_rate: float = 20.0,
        recover_after: float = 600.0,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate <= 0 or burst < 1 or window_cap < burst:
            raise ValueError("rate must be positive, burst at least 1, and the cap at least burst")
        self.base_rate = self.rate = float(rate)
        self.burst, self.window_cap, self.window = burst, window_cap, window
        self._pause, self._min_rate, self._recover_after = pause, min_rate, recover_after
        self._clock, self._sleep = clock, sleep
        self._tokens, self._stamp = float(burst), clock()
        self._sent: deque[tuple[float, int]] = deque()
        self._paused_until = 0.0
        self._last_error = float("-inf")
        self._consecutive = 0
        self.rate_errors = 0

    def used(self) -> int:
        """Changes sent in the last ``window`` seconds."""
        now = self._clock()
        while self._sent and now - self._sent[0][0] >= self.window:
            self._sent.popleft()
        return sum(n for _, n in self._sent)

    async def acquire(self, n: int) -> None:
        """Wait until ``n`` changes may be sent, and count them."""
        if not 1 <= n <= self.burst:
            raise ValueError(f"a chunk holds 1 to {self.burst} changes")
        while True:
            now = self._clock()
            if now < self._paused_until:
                await self._sleep(self._paused_until - now)
                continue
            if self.rate < self.base_rate and now - self._last_error >= self._recover_after:
                self.rate, self._consecutive = self.base_rate, 0
            self._tokens = min(self.burst, self._tokens + (now - self._stamp) * self.rate)
            self._stamp = now
            used = self.used()
            if self._tokens >= n and used + n <= self.window_cap:
                self._tokens -= n
                self._sent.append((now, n))
                return
            wait = 0.0
            if self._tokens < n:
                wait = (n - self._tokens) / self.rate
            if used + n > self.window_cap and self._sent:
                wait = max(wait, self.window - (now - self._sent[0][0]))
            await self._sleep(max(wait, 0.01))

    def on_rate_error(self) -> float:
        """The server said the rate is too high. Returns the pause that starts now, or 0.0
        when the error belongs to a burst that is already answered."""
        now = self._clock()
        self.rate_errors += 1
        first_of_burst = now - self._last_error > 1.0
        if now - self._last_error > 30.0:
            self._consecutive = 0
        self._last_error = now
        if not first_of_burst:
            return 0.0
        self._consecutive += 1
        pause = min(self._pause * self._consecutive, 60.0)
        self._paused_until = max(self._paused_until, now + pause)
        self.rate = max(self.rate / 2.0, self._min_rate)
        self._tokens = 0.0
        return pause

    @property
    def paused_until(self) -> float:
        return self._paused_until
