"""The timings of the surface fit (PRF-05), for ``tools/timings.py surface``.

PRF-05: a surface fit for one underlying runs in the process pool; the event loop is never
blocked for more than 50 ms by numerics. This runs the backend's own parts on the fake broker
(no call leaves the machine): the composition of ``s3t.system.backend`` with the real process
pool, and a watch on each underlying. When the sets have filled, the synthetic market is stopped,
so that its own work on the event loop is not in the timings; the fits are then asked for on
demand. It measures:

- **a fit in the pool,** per underlying: the seconds in the worker, the worker's CPU, and the way
  there and back as the event loop sees it; warm, as the service runs them, and cold. Per slice:
  the fit's time over its slices, and each slice alone in a task of its own, cold.
- **the steps on the event loop:** taking the quotes and making the inputs, choosing the set
  again from the result, and the feed's grant of the demand (``s3t.subs.budget.allocate``). The
  longest step since the watch began is the service's own figure and includes the filling.
- **the lag of a 10 ms timer** on the event loop: with nothing running, while the fits of one
  underlying run one after the other, and while those of all run at once.
- **a worker:** the import a worker of the backend makes for its first task, in a fresh process,
  with its peak memory; and the peak memory of this tool's own workers after the fits. A worker of
  a tool imports the tool's module too (``s3t.system.pool``), so the second is the larger.

Nothing here keeps a price: the output is times, sizes and counts.
"""

import asyncio
import json
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from s3t.analysis import surface_task
from s3t.analysis.surface import SurfaceFit, Watch
from s3t.broker.fake.broker import FakeBroker
from s3t.config import Config, load_config
from s3t.pricing.surface import OK
from s3t.subs.budget import Limits, Wanted, allocate
from s3t.subs.feed import Priority
from s3t.system.backend import Backend, open_backend

type Show = Callable[[str, list[float], str], None]

CONFIG = """
[server]
listen_address = "127.0.0.1"
port = 1
[paths]
env_file = "none.env"
data_dir = "data"
state_file = "data/state.sqlite"
[broker]
source = "fake"
fake_start = 2026-11-20T15:00:00Z
[stream]
stall_after = 3600.0
[store]
daily_copy = false
"""
"""A Friday at 10:00 in New York, so the synthetic market moves. Nothing listens on the port."""

WORKER_PROBE = """
import json, resource, sys, time
t0, c0 = time.perf_counter(), time.process_time()
import s3t.analysis.surface_task
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({
    "import_s": time.perf_counter() - t0, "cpu_s": time.process_time() - c0,
    "peak": peak if sys.platform == "darwin" else peak * 1024,
}))
"""
TIMER_S = 0.010
FILL_LIMIT_S = 600.0
MB = 2**20


def fake_config(directory: Path, own: Config | None) -> Config:
    """The fake broker in a temporary directory, with the owner's ``[numerics]``, ``[surface]``
    and ``[dividends]`` when a configuration is given."""
    path = directory / "s3t.toml"
    path.write_text(CONFIG)
    config = load_config(path)
    if own is None:
        return config
    sections = {"numerics": own.numerics, "surface": own.surface, "dividends": own.dividends}
    return config.model_copy(update=sections)


def worker_probe(runs: int) -> list[dict[str, float]]:
    """What a worker of the backend imports for its first task, in fresh processes."""
    found = []
    for _ in range(runs):
        out = subprocess.run(
            [sys.executable, "-c", WORKER_PROBE], check=True, capture_output=True, text=True
        )
        found.append(json.loads(out.stdout.strip().splitlines()[-1]))
    return found


class Lag:
    """A timer of 10 ms on the event loop, and by how much each turn came late."""

    def __init__(self) -> None:
        self.ms: list[float] = []
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while True:
            due = time.perf_counter() + TIMER_S
            await asyncio.sleep(TIMER_S)
            self.ms.append((time.perf_counter() - due) * 1000)

    async def __aenter__(self) -> "Lag":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self._task is not None
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


async def _until(done: Callable[[], bool], seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while not done():
        if time.monotonic() > deadline:
            raise SystemExit(f"{what} did not come within {seconds:.0f} s")
        await asyncio.sleep(0.05)


def _ms(fits: Sequence[SurfaceFit], field: str) -> list[float]:
    return [getattr(fit, field) * 1000 for fit in fits]


async def _fill(backend: Backend, watches: dict[str, Watch]) -> None:
    """Wait until every set has filled, and say how long that took on the fake."""
    started = time.perf_counter()
    first: dict[str, float] = {}
    full: dict[str, float] = {}
    seen: dict[str, SurfaceFit] = {}

    def filled() -> bool:
        for status in backend.surface.status().watched:
            name, watch = status.underlying, watches[status.underlying]
            if watch.latest is None and status.problem is not None:
                raise SystemExit(f"{name}: {status.problem}")
            if watch.latest is not None:
                first.setdefault(name, time.perf_counter() - started)
                seen.setdefault(name, watch.latest)
                if not status.filling:
                    full.setdefault(name, time.perf_counter() - started)
        return len(full) == len(watches)

    await _until(filled, FILL_LIMIT_S, "the surfaces")
    print("the filling, on the fake: the manager sends 150 subscriptions a second")
    for name in watches:
        fit = seen[name]
        print(
            f"  {name}: a fit {first[name]:.1f} s after the watch (number {fit.number}, "
            f"{fit.took_s * 1000:.0f} ms there and back, with the start of a worker if it was "
            f"the first), the set filled after {full[name]:.1f} s"
        )


async def _fits(watch: Watch, runs: int) -> list[SurfaceFit]:
    return [await watch.fit() for _ in range(runs)]


async def _one_underlying(backend: Backend, watch: Watch, runs: int, show: Show) -> None:
    name = watch.underlying
    status = next(w for w in backend.surface.status().watched if w.underlying == name)
    by_status = ", ".join(f"{count} {key}" for key, count in sorted(status.slices.items()))
    print(
        f"\n{name}: {status.pairs} pairs, {status.expirations} expirations ({by_status}); "
        f"longest step on the event loop since the watch began {status.loop_ms_max:.2f} ms"
    )
    cpu, wall = time.thread_time(), time.perf_counter()
    async with Lag() as lag:
        fits = await _fits(watch, runs)
    cpu, wall = time.thread_time() - cpu, time.perf_counter() - wall
    slices = len(fits[-1].surface.slices)
    own = sum(s.status == OK for s in fits[-1].surface.slices)
    show(f"{name} fit, warm: in the worker", _ms(fits, "fit_s"), "ms")
    show("  the worker's CPU", _ms(fits, "cpu_s"), "ms")
    show("  there and back, on the loop", _ms(fits, "took_s"), "ms")
    show(f"  per slice ({slices} slices)", [x / slices for x in _ms(fits, "fit_s")], "ms")
    if own:
        show(f"  per slice with its own SVI ({own})", [x / own for x in _ms(fits, "fit_s")], "ms")
    show("  loop: quotes to inputs", _ms(fits, "gather_s"), "ms")
    show("  loop: the set chosen again", _ms(fits, "select_s"), "ms")
    show("  lag of the 10 ms timer", lag.ms, "ms")
    print(f"  the event loop's thread used {cpu / wall * 100:.1f}% of a core while they ran")

    # Cold: the same inputs without the fit before, and each slice in a task of its own.
    state = backend.surface._watched[name]
    inputs = backend.surface._gather(state, backend.broker.data_time())[0]
    cold = [await backend.pool.run(surface_task.fit, inputs, None, None) for _ in range(3)]
    show(f"{name} fit, cold: in the worker", [f.wall_s * 1000 for f in cold], "ms")
    show("  the worker's CPU", [f.cpu_s * 1000 for f in cold], "ms")
    alone = [await backend.pool.run(surface_task.fit, [one], None, None) for one in inputs]
    fitted = [f.wall_s * 1000 for f in alone if f.surface.slices[0].status == OK]
    other = [f.wall_s * 1000 for f in alone if f.surface.slices[0].status != OK]
    if fitted:
        show("  a slice alone, its own SVI", fitted, "ms")
    if other:
        show("  a slice alone, no SVI of its own", other, "ms")


def _grant(backend: Backend, show: Show) -> None:
    """The feed's grant of the demand as it stands: its step on the event loop."""
    feed = backend.feed
    consumers = feed._consumers  # the tool looks inside, as its neighbours do
    wanted = [Wanted(d.name, d.priority, d.subs) for d in consumers.values()]
    pairs = sum(len(w.subs) for w in wanted)
    limits = Limits(backend.config.subscriptions.budget, backend.config.subscriptions.candle_budget)
    took = []
    for _ in range(20):
        started = time.perf_counter()
        allocate(wanted, limits, backend.broker.data_time())
        took.append((time.perf_counter() - started) * 1000)
    assert all(w.priority is Priority.VIEWS for w in wanted)
    show(f"loop: the feed's grant of {pairs} pairs", took, "ms")


async def measure(backend: Backend, symbols: Sequence[str], runs: int, show: Show) -> None:
    fake = backend.broker
    assert isinstance(fake, FakeBroker)
    await _until(lambda: backend.feed.status().stream.phase == "connected", 30, "the fake stream")
    watches = {name: backend.surface.watch(name) for name in symbols}
    await _fill(backend, watches)
    await asyncio.wait_for(backend.feed.settled(), 120)
    # The synthetic market stops here: its ticks are work of the fake on the event loop.
    fake.dxlink.tick = lambda: None  # type: ignore[method-assign]
    await asyncio.sleep(2.5)  # the last flush
    for watch in watches.values():  # nothing has changed after this one, so the timer skips
        await watch.fit()
    await _until(lambda: backend.pool.status().under_way == 0, 60, "the end of the fits")
    await asyncio.wait_for(backend.feed.settled(), 120)
    feed = backend.feed.status()
    print(f"pairs granted: {feed.granted} of {feed.budget}, {feed.pending} still to send")

    async with Lag() as idle:
        await asyncio.sleep(3.0)
    show("lag of the 10 ms timer, nothing running", idle.ms, "ms")
    _grant(backend, show)

    for watch in watches.values():
        await _one_underlying(backend, watch, runs, show)

    if len(watches) > 1:
        print(f"\nall {len(watches)} at once, {runs} rounds:")
        cpu, wall = time.thread_time(), time.perf_counter()
        async with Lag() as lag:
            rounds = [
                await asyncio.gather(*(watch.fit() for watch in watches.values()))
                for _ in range(runs)
            ]
        cpu, wall = time.thread_time() - cpu, time.perf_counter() - wall
        show("  a round, there and back", [max(_ms(r, "took_s")) for r in rounds], "ms")
        show("  lag of the 10 ms timer", lag.ms, "ms")
        print(f"  the event loop's thread used {cpu / wall * 100:.1f}% of a core while they ran")

    usage = await asyncio.gather(
        *(backend.pool.run(resource.getrusage, resource.RUSAGE_SELF) for _ in range(8))
    )
    scale = 1 if sys.platform == "darwin" else 1024
    peak = max(u.ru_maxrss for u in usage) * scale / MB
    pool = backend.pool.status()
    print(
        f"\nthe pool at the end: {pool.processes} processes, {pool.done} tasks done, "
        f"{pool.failed} failed, {pool.broken} broken; peak memory of a worker of this tool "
        f"{peak:.0f} MB"
    )
    for watch in watches.values():
        watch.release()


async def surface(own: Config | None, symbols: Sequence[str], runs: int, show: Show) -> int:
    probes = worker_probe(5)
    show("a worker's import, fresh process", [p["import_s"] * 1000 for p in probes], "ms")
    show("  its CPU", [p["cpu_s"] * 1000 for p in probes], "ms")
    show("  peak memory after it", [p["peak"] / MB for p in probes], "MB")
    with tempfile.TemporaryDirectory(prefix="s3t-timings-") as directory:
        config = fake_config(Path(directory), own)
        print(
            f"[numerics] workers = {config.numerics.workers}; [surface] {config.surface!r}; "
            "the synthetic market"
        )
        async with open_backend(config) as backend:
            await measure(backend, symbols, runs, show)
    return 0
