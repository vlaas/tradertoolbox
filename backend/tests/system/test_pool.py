"""The process pool (PRF-05): real worker processes, started by the first task.

The functions that run in a worker are the standard library's: a worker imports the module of the
function it is given, and it must find it without the tests on its path.
"""

import asyncio
import logging
import math
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from s3t.system.pool import PARENT_CHECK_S, NumericsPool, PoolBroken

pytestmark = pytest.mark.anyio

BACKEND = Path(__file__).resolve().parents[2]
AT = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone(pid: int, within: float) -> bool:
    """Whether a process ends within so many seconds. Blocking."""
    deadline = time.monotonic() + within
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not alive(pid)


async def test_nothing_runs_before_the_first_task() -> None:
    pool = NumericsPool(2, now=lambda: AT)
    status = pool.status()
    assert status.started_at is None and status.processes == 0 and pool.pids() == ()
    assert (status.workers, status.under_way, status.done) == (2, 0, 0)
    await pool.aclose()  # nothing to end
    assert pool.status().started_at is None


async def test_a_task_runs_in_another_process_and_one_process_serves_them_in_turn() -> None:
    pool = NumericsPool(2, now=lambda: AT)
    try:
        first = await pool.run(os.getpid)
        assert first != os.getpid() and pool.pids() == (first,)
        assert await pool.run(os.getpid) == first  # no second process for a task at a time
        assert await pool.run(math.hypot, 3.0, 4.0) == 5.0
        status = pool.status()
        assert status.started_at == AT and status.processes == 1
        assert (status.done, status.failed, status.broken, status.under_way) == (3, 0, 0, 0)
    finally:
        await pool.aclose()
    assert not alive(first)
    with pytest.raises(RuntimeError, match="closed"):
        await pool.run(os.getpid)


async def test_tasks_at_once_get_a_process_each_up_to_the_number_of_workers() -> None:
    pool = NumericsPool(2)
    try:
        await asyncio.gather(*(pool.run(time.sleep, 0.3) for _ in range(3)))
        assert len(pool.pids()) == 2 and pool.status().done == 3
    finally:
        await pool.aclose()


async def test_the_error_of_a_function_comes_out_as_it_is() -> None:
    pool = NumericsPool(1)
    try:
        with pytest.raises(ValueError, match="math domain error"):
            await pool.run(math.sqrt, -1.0)
        status = pool.status()
        assert (status.done, status.failed, status.broken) == (0, 1, 0)
        assert await pool.run(math.sqrt, 4.0) == 2.0  # the worker goes on
    finally:
        await pool.aclose()


async def test_a_worker_that_dies_fails_its_task_and_the_next_one_gets_a_new_pool(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = NumericsPool(1)
    try:
        before = await pool.run(os.getpid)
        with pytest.raises(PoolBroken):
            await pool.run(os._exit, 3)
        assert pool.status().broken == 1 and pool.pids() == ()
        assert "a worker process of the pool ended" in caplog.text
        after = await pool.run(os.getpid)
        assert after != before and pool.status().processes == 1
    finally:
        await pool.aclose()


async def test_a_worker_is_quiet_and_ignores_the_terminal_interrupt(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The ported solver warns through its logger. Without a handler in the worker the line would
    reach the terminal of the backend."""
    pool = NumericsPool(1)
    try:
        await pool.run(logging.getLogger("s3t.pricing.bsm").warning, "canary-of-the-worker")
        assert await pool.run(signal.getsignal, signal.SIGINT) == signal.SIG_IGN
    finally:
        await pool.aclose()
    assert "canary-of-the-worker" not in "".join(capfd.readouterr())


ORPHAN = """
import asyncio, os
from s3t.system.pool import NumericsPool

async def main():
    pool = NumericsPool(1)
    print(await pool.run(os.getpid), flush=True)
    await asyncio.sleep(60)

asyncio.run(main())
"""


async def test_a_worker_ends_when_the_backend_is_killed() -> None:
    """An executor's worker waits for work for ever when its parent is gone. Ours looks."""
    parent = await asyncio.create_subprocess_exec(
        sys.executable, "-c", ORPHAN, cwd=BACKEND, stdout=subprocess.PIPE
    )
    try:
        assert parent.stdout is not None
        worker = int(await asyncio.wait_for(parent.stdout.readline(), 30))
        assert alive(worker)
    finally:
        parent.kill()
        await parent.wait()
    assert await asyncio.to_thread(gone, worker, PARENT_CHECK_S + 10)
