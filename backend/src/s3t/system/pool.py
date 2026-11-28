"""The process pool for heavy numerics (PRF-05).

PRF-05: a surface fit for one underlying runs in the process pool; the event loop is never blocked
for more than 50 ms by numerics. Decided 2026-11-27 (owner, WP-1.1):

- A ``ProcessPoolExecutor`` with the start method ``spawn`` on every platform, and
  ``[numerics] workers`` processes.
- **Nothing is started with the backend.** The executor is built by the first task, and it starts
  a process only when a task finds no idle one. A backend in which nothing asks for a fit has no
  worker process.
- **This module imports no numerics.** It runs the function it is given, by reference: a
  module-level function, with plain values as arguments and result, since both cross a process
  boundary. A worker imports the module of that function and nothing of the backend.
- **A worker is quiet.** It gets a null log handler: the ported solver warns through its logger for
  a quote it cannot invert, and without a handler those lines reach the terminal. It ignores
  SIGINT, which a terminal sends to every process of the group: the backend ends its workers.
- **A worker does not outlive the backend.** An executor's workers wait for work for ever when
  their parent is killed outright (seen 2026-11-27). Each worker therefore looks every few seconds
  whether its parent is still there, and ends when it is not.

A worker that dies breaks the executor (the standard library's rule). The task that saw it fails
with :class:`PoolBroken`, and the next task builds a new executor. A task has no time limit: one
task cannot be ended without ending its process, and the fits are bounded by their own iteration
limits.

With ``spawn`` a worker runs the main module of its parent again, unless that is the
``__main__`` of a package (the standard library's rule). So a worker of ``python -m s3t`` imports
nothing of the backend, and a worker of a tool that was started by its path imports that tool.

The memory of the workers is not the backend's: PRF-09 bounds the backend's own process
(``s3t.system.process``).
"""

import asyncio
import logging
import multiprocessing
import os
import signal
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

PARENT_CHECK_S = 2.0
"""How often a worker looks whether its parent is still there."""

LOG = logging.getLogger(__name__)


class PoolBroken(Exception):
    """A worker process ended while the task was under way or waiting. The task was not done."""


class Pool(Protocol):
    """What a service needs of the pool. A test gives one that runs the function in place."""

    async def run[**P, T](self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
        """Run a module-level function in a worker process and return its result. An error of
        the function comes out as it is. Raises :class:`PoolBroken`."""
        ...


@dataclass(frozen=True)
class PoolStatus:
    """For the health route (SEC-08)."""

    workers: int
    """The configured number of worker processes: the most that run at once."""
    started_at: datetime | None
    """UTC. When the first task built the executor; None while no process was ever started."""
    processes: int
    """Worker processes alive now."""
    under_way: int
    """Tasks that run or wait for a worker."""
    done: int
    failed: int
    """Tasks whose function raised."""
    broken: int
    """Times a worker died and the executor was given up. Not zero means a fault."""


def _watch_parent(parent: int) -> None:
    while os.getppid() == parent:
        time.sleep(PARENT_CHECK_S)
    os._exit(0)


def _quiet_worker(parent: int) -> None:
    """Runs in each worker when it starts."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    logging.getLogger().addHandler(logging.NullHandler())
    threading.Thread(target=_watch_parent, args=(parent,), daemon=True, name="parent").start()


class NumericsPool:
    def __init__(
        self, workers: int, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._workers, self._now = workers, now
        self._executor: ProcessPoolExecutor | None = None
        self._started_at: datetime | None = None
        self._under_way = self._done = self._failed = self._broken = 0
        self._closed = False

    def __repr__(self) -> str:
        return f"NumericsPool({self._workers} workers, {self._state()})"

    def _state(self) -> str:
        return "closed" if self._closed else "idle" if self._executor is None else "started"

    def status(self) -> PoolStatus:
        return PoolStatus(
            workers=self._workers,
            started_at=self._started_at,
            processes=len(self.pids()),
            under_way=self._under_way,
            done=self._done,
            failed=self._failed,
            broken=self._broken,
        )

    def pids(self) -> tuple[int, ...]:
        """The process ids of the workers alive now."""
        executor = self._executor
        if executor is None:
            return ()
        # The executor has no public list of its processes.
        processes: dict[int, multiprocessing.Process] = getattr(executor, "_processes", None) or {}
        return tuple(pid for pid, process in list(processes.items()) if process.is_alive())

    def _start(self) -> ProcessPoolExecutor:
        executor = ProcessPoolExecutor(
            max_workers=self._workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_quiet_worker,
            initargs=(os.getpid(),),
        )
        if self._started_at is None:
            self._started_at = self._now()
        LOG.info("the process pool starts, with at most %d workers", self._workers)
        return executor

    async def run[**P, T](self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
        if self._closed:
            raise RuntimeError("the process pool is closed")
        if self._executor is None:
            self._executor = self._start()
        executor = self._executor
        self._under_way += 1
        try:
            result = await asyncio.wrap_future(executor.submit(fn, *args, **kwargs))
        except BrokenProcessPool:
            self._give_up(executor)
            raise PoolBroken("a worker process of the pool ended") from None
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed += 1
            raise
        finally:
            self._under_way -= 1
        self._done += 1
        return result

    def _give_up(self, executor: ProcessPoolExecutor) -> None:
        """The executor is broken: the next task builds a new one."""
        if self._executor is executor:
            self._executor = None
            self._broken += 1
            LOG.error("a worker process of the pool ended; the pool starts anew with the next task")
            executor.shutdown(wait=False, cancel_futures=True)

    async def aclose(self) -> None:
        """End the workers. A task under way is finished first; one that waits is dropped."""
        self._closed = True
        executor, self._executor = self._executor, None
        if executor is not None:
            # In a thread: it waits for the processes.
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
