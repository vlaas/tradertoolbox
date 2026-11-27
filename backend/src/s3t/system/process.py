"""The memory of the process against its configured bound (PRF-09).

PRF-09: the backend runs a full trading day without restart and without growth of memory beyond a
configured bound. The bound is ``[process] memory_bound_mb``. A watch reads the peak resident memory
of the process once a minute, and logs one warning when it goes above the bound. The health route
reports it (SEC-08). Nothing else happens: the backend does not restart itself, and the app does not
show it yet.

The peak is the operating system's high-water mark (``getrusage``), so it never falls. A peak that
keeps rising over a day is growth; one that levels off is not. The current resident memory needs a
platform call or a package, and the day run of WP-0.9 reads it from outside.

Decided 2026-11-27 (owner, WP-0.9): the bound limits nothing, it is observed; 1,024 MB is enough.
"""

import asyncio
import logging
import resource
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

CHECK_EVERY_S = 60.0
MB = 1024 * 1024

LOG = logging.getLogger(__name__)


def peak_rss_bytes() -> int:
    """The peak resident memory of this process so far, in bytes."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS states bytes, Linux kibibytes.
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


@dataclass(frozen=True)
class ProcessStatus:
    """For the health route (SEC-08)."""

    peak_rss_mb: float
    """The peak resident memory at the last check, in MiB."""
    bound_mb: int
    """The configured bound, in MiB (PRF-09)."""
    over_bound_since: datetime | None
    """UTC. The check that first found the peak above the bound; None while it is below."""
    checked_at: datetime | None
    """UTC. The last check."""


class ProcessWatch:
    """Checks the peak memory against the bound, at the start and then once a minute."""

    def __init__(
        self,
        bound_mb: int,
        *,
        read: Callable[[], int] = peak_rss_bytes,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        every: float = CHECK_EVERY_S,
    ) -> None:
        self._bound, self._read, self._now, self._sleep = bound_mb, read, now, sleep
        self._every = every
        self._peak_mb = 0.0
        self._over_since: datetime | None = None
        self._checked_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    def status(self) -> ProcessStatus:
        return ProcessStatus(
            round(self._peak_mb, 1), self._bound, self._over_since, self._checked_at
        )

    def check(self) -> None:
        self._peak_mb = self._read() / MB
        self._checked_at = self._now()
        if self._peak_mb > self._bound and self._over_since is None:
            self._over_since = self._checked_at
            LOG.warning(
                "the peak memory of the process, %.0f MB, is above the bound of %d MB (PRF-09)",
                self._peak_mb,
                self._bound,
            )

    def start(self) -> None:
        if self._task is None:
            self.check()
            self._task = asyncio.create_task(self._run(), name="process-watch")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            await self._sleep(self._every)
            self.check()
