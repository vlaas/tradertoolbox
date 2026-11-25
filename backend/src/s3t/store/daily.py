"""A copy of the state file once a day, while the backend runs (STO-07).

Proposed 2026-11-25 (WP-0.7). The newest copies are kept, a configured number of them
(``[store] daily_copies``). This is the only thing that deletes a copy, and it deletes none but
its own: the name of a daily copy holds the mark ``daily``, and the copies made by hand or before
a migration do not.

A copy is made when the newest one is a day old, so a backend that is started several times a day
does not fill its places with copies of one day. The names hold the time, so nothing else is kept.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from s3t.store.backup import backup, backup_name
from s3t.store.migrate import StoreError

MARK = "daily"
EVERY = timedelta(days=1)
LOOK_EVERY_S = 600.0

LOG = logging.getLogger(__name__)

type Run = Callable[..., Awaitable[Any]]
"""Runs a blocking function elsewhere: ``asyncio.to_thread``."""
type Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class CopiesStatus:
    """For the health route (SEC-08)."""

    keep: int
    """How many are kept; 0 makes none."""
    last_at: datetime | None
    """UTC. The newest daily copy that is there."""
    failures: int
    """Failed attempts since then, in this process."""
    last_error: str | None


class DailyCopies:
    def __init__(
        self,
        state_file: Path,
        directory: Path,
        keep: int,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        run: Run = asyncio.to_thread,
    ) -> None:
        self._file, self._dir, self._keep = state_file, directory, keep
        self._now, self._sleep, self._in_thread = now, sleep, run
        self._name = re.compile(rf"{re.escape(state_file.stem)}-{MARK}-(\d{{8}}T\d{{6}}Z)\.sqlite")
        self._last_at: datetime | None = None
        self._failures = 0
        self._error: str | None = None
        self._task: asyncio.Task[None] | None = None

    def status(self) -> CopiesStatus:
        return CopiesStatus(self._keep, self._last_at, self._failures, self._error)

    def start(self) -> None:
        """Make the copies, in a task of its own. Does nothing when none are wanted."""
        if self._keep and self._task is None:
            self._task = asyncio.create_task(self._run(), name="daily-copies")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            await self.step()
            await self._sleep(LOOK_EVERY_S)

    async def step(self) -> None:
        """Make a copy if it is time for one. No error comes out of it: the status has it."""
        try:
            self._last_at = await self._in_thread(self._copy_if_due, self._now())
        except Exception as e:
            self._failures += 1
            # The texts hold paths, which belong to the machine; the log has them.
            self._error = "the daily copy failed; the log says why"
            if not isinstance(e, StoreError | OSError):
                LOG.exception("state store: the daily copy failed")
            elif self._failures == 1:
                LOG.warning("state store: the daily copy failed: %s", e)
        else:
            self._failures, self._error = 0, None

    # ------------------------------------------------------------------ in a thread

    def _copy_if_due(self, now: datetime) -> datetime | None:
        # A copy from a time to come does not count: the clock was wrong when it was made.
        newest = max((at for at, _ in self._copies() if at <= now), default=None)
        if newest is None or now - newest >= EVERY:
            report = backup(self._file, self._dir / backup_name(self._file, now, MARK))
            LOG.info("state store: daily copy %s, %d bytes", report.path.name, report.size)
            newest = now
            for _, path in self._copies()[: -self._keep]:
                path.unlink()
        return newest

    def _copies(self) -> list[tuple[datetime, Path]]:
        """The daily copies that are there, the oldest first."""
        found: list[tuple[datetime, Path]] = []
        if self._dir.is_dir():
            for path in self._dir.iterdir():
                m = self._name.fullmatch(path.name)
                if m is not None:
                    at = datetime.strptime(m[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                    found.append((at, path))
        return sorted(found)
