"""A copy of the state file once a day, while the backend runs (STO-07).

Decided 2026-11-26 (owner, WP-0.7 review): every copy is kept, and nothing is deleted. The copies
lie in a folder per year beside the others (``s3t.store.backup.copy_path``), and the mark ``daily``
in the name tells them from those made before a migration or by hand. ``[store] daily_copy``
switches them off.

A copy is made when the newest daily copy is a day old, so a backend that is started several times
a day makes one a day, not one a start.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from s3t.store.backup import backup, copy_path
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

    on: bool
    """Whether daily copies are made."""
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
        *,
        on: bool = True,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        run: Run = asyncio.to_thread,
    ) -> None:
        self._file, self._dir, self._on = state_file, directory, on
        self._now, self._sleep, self._in_thread = now, sleep, run
        self._name = re.compile(rf"{re.escape(state_file.stem)}-{MARK}-(\d{{8}}T\d{{6}}Z)\.sqlite")
        self._last_at: datetime | None = None
        self._failures = 0
        self._error: str | None = None
        self._task: asyncio.Task[None] | None = None

    def status(self) -> CopiesStatus:
        return CopiesStatus(self._on, self._last_at, self._failures, self._error)

    def start(self) -> None:
        """Make the copies, in a task of its own. Does nothing when they are switched off."""
        if self._on and self._task is None:
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
        newest = max((at for at in self._made() if at <= now), default=None)
        if newest is None or now - newest >= EVERY:
            report = backup(self._file, copy_path(self._dir, self._file, now, MARK))
            LOG.info("state store: daily copy %s, %d bytes", report.path.name, report.size)
            newest = now
        return newest

    def _made(self) -> list[datetime]:
        """When the daily copies that are there were made, in the folders of the years."""
        found: list[datetime] = []
        for path in self._dir.glob("[0-9][0-9][0-9][0-9]/*.sqlite"):
            m = self._name.fullmatch(path.name)
            if m is not None:
                found.append(datetime.strptime(m[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
        return found
