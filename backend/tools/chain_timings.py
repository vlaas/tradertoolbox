"""The timings of the chain's flush (PRF-03), for ``tools/timings.py chain``.

PRF-03: a flush that recomputes IV and Greeks for 1,000 changed contracts takes under 100 ms and
never delays the stream reader. The flush runs on the event loop, so PRF-05's 50 ms bound holds
for it too (plan, section 8). This runs the backend's own parts: the composition of
``s3t.system.backend`` with the chain service and the surface service, on the fake broker (no call
leaves the machine) and on a local recording. It measures:

- **the flush on the synthetic market:** views of SPY and SPX whose windows hold more than 1,000
  contracts, while the market moves every second. Per flush with contracts due: their count, and
  the wall and CPU time of the flush, the gathering of the inputs and the one call of the pricing
  core included (``ChainService.timings``). The parts the views make from it are timed apart,
  and the encoding of each into its wire message.
- **the reader beside it:** the wait of a batch from its receipt to its take by a reader of the
  feed, the lag of a 10 ms timer, and the batches the market state lost.
- **the call alone:** the pricing core on exactly 1,000 of the held contracts, and the whole
  flush with all of them due, each repeated.
- **a recording replayed** (``--recording NAME``): the same views on the windows that the
  recording holds, at multiples of its pace.

Nothing here keeps a price: the output is times, sizes and counts.
"""

import asyncio
import math
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from s3t.analysis.chain import ChainView, Part, Window
from s3t.api.chain_rows import chain_message
from s3t.broker.fake.broker import FakeBroker
from s3t.broker.interface import EventBatch, EventType, StreamState
from s3t.broker.replay import ReplayBroker
from s3t.config import Config
from s3t.system.backend import Backend, open_backend
from surface_timings import Lag, fake_config

type Show = Callable[[str, list[float], str], None]

WIDE = 120
"""Strikes of a window: the most a view may hold."""
SYNTHETIC = {"SPY": ("SPY", 4), "SPX": ("SPXW", 5)}
"""The roots and the number of their first expirations, from 7 days on, that get a window."""
FILL_LIMIT_S = 300.0


class Consumer:
    """Takes the parts of a view, as the topic does, and times what it does with them."""

    def __init__(self, view: ChainView) -> None:
        self.view = view
        self.part_ms: list[float] = []
        self.encode_ms: list[float] = []
        self.rows: list[float] = []
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        view = self.view
        make = view._part

        def timed() -> Part:
            started = time.perf_counter()
            part = make()
            self.part_ms.append((time.perf_counter() - started) * 1000)
            return part

        view._part = timed  # type: ignore[method-assign]
        while True:
            try:
                part = await view.next()
            except RuntimeError:  # the view was closed
                return
            started = time.perf_counter()
            chain_message(part).SerializeToString()
            self.encode_ms.append((time.perf_counter() - started) * 1000)
            self.rows.append(float(len(part.rows)))

    async def aclose(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self.view.close()


class Waits:
    """The wait of each batch from its receipt to its take, by a reader of its own."""

    def __init__(self, backend: Backend) -> None:
        self.ms: list[float] = []
        self._now = backend.market.now
        self._reader = backend.feed.reader("timings-waits", maxsize=1_000_000)
        self._task = asyncio.create_task(self._run())
        self.ended = asyncio.Event()

    async def _run(self) -> None:
        async for item in self._reader:
            if isinstance(item, EventBatch):
                self.ms.append((self._now() - item.received_at).total_seconds() * 1000)
            elif isinstance(item, StreamState) and item.phase == "disconnected":
                self.ended.set()

    async def aclose(self) -> None:
        self._reader.close()
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


async def _until(done: Callable[[], bool], seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while not done():
        if time.monotonic() > deadline:
            raise SystemExit(f"{what} did not come within {seconds:.0f} s")
        await asyncio.sleep(0.1)


async def _keys(backend: Backend, symbol: str, root: str, count: int) -> list[str]:
    chain = await backend.instruments.chain(symbol)
    today = backend.market.now().astimezone(ZoneInfo("America/New_York")).date()
    found = [
        e for e in chain.expirations if e.root == root and (e.expiration_date - today).days >= 7
    ]
    return [f"{e.root}:{e.expiration_date.isoformat()}" for e in found[:count]]


def _computed(backend: Backend) -> bool:
    """Every held contract has been computed once."""
    cells = [c for b in backend.chain._books.values() for c in b.cells.values()]
    return bool(cells) and all(c.at is not None or c.exp.over for c in cells)


def _contracts(backend: Backend) -> int:
    return sum(u.contracts for u in backend.chain.status().underlyings)


async def _views(backend: Backend, wanted: dict[str, list[Window]]) -> list[Consumer]:
    consumers = []
    for symbol, windows in wanted.items():
        view = backend.chain.open(symbol, windows)
        await asyncio.wait_for(view.ready(), 60)
        view.snapshot()
        consumers.append(Consumer(view))
    return consumers


def _report(
    backend: Backend, consumers: Sequence[Consumer], waits: Waits, lag: Lag, show: Show
) -> None:
    timings = list(backend.chain.timings)
    show("contracts computed per flush", [float(n) for n, _, _ in timings], "")
    show("a flush with contracts due, wall", [w * 1000 for _, w, _ in timings], "ms")
    show("  its CPU", [c * 1000 for _, _, c in timings], "ms")
    big = [(n, w, c) for n, w, c in timings if n >= 900]
    if big:
        show("  of them with 900 or more contracts, wall", [w * 1000 for _, w, _ in big], "ms")
        show("  and their CPU", [c * 1000 for _, _, c in big], "ms")
        per = [w * 1e6 / n for n, w, _ in big]
        show("  wall per contract", per, "µs")
    show("a view's part made from it", [x for c in consumers for x in c.part_ms], "ms")
    show("  rows of a part", [x for c in consumers for x in c.rows], "")
    show("  its wire message encoded", [x for c in consumers for x in c.encode_ms], "ms")
    show("the wait of a batch, receipt to take", waits.ms, "ms")
    show("the lag of a 10 ms timer", lag.ms, "ms")
    print(f"batches the market state lost: {backend.market.status().lost}")
    print(
        f"flushes with contracts due: {len(timings)}; the most contracts in one: "
        f"{max((n for n, _, _ in timings), default=0)}"
    )


def _time_the_fake(backend: Backend) -> list[float]:
    """The fake stream makes the events of every subscribed pair once a second, on the event
    loop. That is the fake's work and not the application's, but it delays the same loop."""
    fake = backend.broker
    assert isinstance(fake, FakeBroker)
    found: list[float] = []
    tick = fake.dxlink.tick

    def timed() -> None:
        started = time.perf_counter()
        tick()
        found.append((time.perf_counter() - started) * 1000)

    fake.dxlink.tick = timed  # type: ignore[method-assign]
    return found


def _bare(backend: Backend, runs: int, show: Show) -> None:
    """The call alone on 1,000 contracts, and a whole flush with all held contracts due."""
    service = backend.chain
    cells = [c for b in service._books.values() for c in b.cells.values() if not c.exp.over]
    if not cells:
        print("no contract is held")
        return
    thousand = (cells * math.ceil(1000 / len(cells)))[:1000]
    what = "1,000 contracts" if len(cells) >= 1000 else f"1,000 rows of the {len(cells)} held"
    now = backend.market.now()
    wall, cpu = [], []
    for _ in range(runs):
        started, used = time.perf_counter(), time.thread_time()
        service._compute(thousand, now)
        wall.append((time.perf_counter() - started) * 1000)
        cpu.append((time.thread_time() - used) * 1000)
    show(f"the call alone, {what}, wall", wall, "ms")
    show("  its CPU", cpu, "ms")
    wall, cpu = [], []
    for _ in range(runs):
        for c in cells:
            c.at = None  # all of them due
        started, used = time.perf_counter(), time.thread_time()
        service._flush({}, backend.market.now())
        wall.append((time.perf_counter() - started) * 1000)
        cpu.append((time.thread_time() - used) * 1000)
    show(f"a whole flush, all {len(cells)} held contracts due, wall", wall, "ms")
    show("  its CPU", cpu, "ms")


async def synthetic(own: Config | None, seconds: float, runs: int, show: Show) -> int:
    with tempfile.TemporaryDirectory(prefix="s3t-timings-") as directory:
        config = fake_config(Path(directory), own)
        async with open_backend(config) as backend:
            wanted: dict[str, list[Window]] = {}
            for symbol, (root, count) in SYNTHETIC.items():
                wanted[symbol] = [Window(key) for key in await _keys(backend, symbol, root, count)]
            # A window as wide as a view may hold, around the money.
            await asyncio.sleep(2)
            for symbol, windows in wanted.items():
                widened = []
                for w in windows:
                    snapshot = await backend.chain.expiration(symbol, w.expiration)
                    strikes = [r.strike for r in snapshot.rows]
                    money = snapshot.expiration.forward
                    if not math.isfinite(money):
                        money = snapshot.head.spot
                    i = min(range(len(strikes)), key=lambda k: abs(strikes[k] - money))
                    lo = max(0, i - WIDE // 2)
                    hi = min(len(strikes), lo + WIDE) - 1
                    widened.append(Window(w.expiration, strikes[lo], strikes[hi]))
                wanted[symbol] = widened
            consumers = await _views(backend, wanted)
            await _until(lambda: _contracts(backend) >= 1000, 60, "1,000 held contracts")
            await _until(lambda: _computed(backend), FILL_LIMIT_S, "a value of every contract")
            await asyncio.sleep(10)  # the surface's sets fill
            print(f"held contracts: {_contracts(backend)}; the synthetic market, moving")
            backend.chain.timings.clear()
            for c in consumers:
                c.part_ms.clear()
                c.encode_ms.clear()
                c.rows.clear()
            waits = Waits(backend)
            ticks = _time_the_fake(backend)
            async with Lag() as lag:
                await asyncio.sleep(seconds)
            _report(backend, consumers, waits, lag, show)
            show("the synthetic market's own tick, on the same loop", ticks, "ms")
            await waits.aclose()
            _bare(backend, runs, show)
            for c in consumers:
                await c.aclose()
    return 0


async def recorded(own: Config, name: str, speeds: Sequence[float], show: Show) -> int:
    """The recording is read from the configuration's directory of recordings; the state file
    and everything else is a temporary one's."""
    from timings import recorded_pairs

    for speed in speeds:
        with tempfile.TemporaryDirectory(prefix="s3t-timings-") as directory:
            config = fake_config(Path(directory), own)
            broker = ReplayBroker(own.paths.recordings_dir / name, speed=speed, paused=True)
            quoted = {p.symbol for p in recorded_pairs(broker) if p.event is EventType.QUOTE}
            async with open_backend(config, broker=broker) as backend:
                wanted: dict[str, list[Window]] = {}
                for symbol in sorted({s for s in quoted if not s.startswith(".")}):
                    try:
                        chain = await broker.option_chain(symbol)
                    except Exception:
                        continue
                    for e in chain.expirations:
                        held = [
                            k
                            for k in range(len(e.strikes))
                            if e.call_streams[k] in quoted or e.put_streams[k] in quoted
                        ]
                        if held:
                            key = f"{e.root}:{e.expiration_date.isoformat()}"
                            low, high = e.strikes[held[0]], e.strikes[held[-1]]
                            wanted.setdefault(symbol, []).append(Window(key, low, high))
                consumers = await _views(backend, wanted)
                await backend.feed.settled()
                print(
                    f"recording {name} at {speed:g} times its pace: held contracts "
                    f"{_contracts(backend)}"
                )
                waits = Waits(backend)
                broker.resume()
                async with Lag() as lag:
                    await asyncio.wait_for(waits.ended.wait(), 3600)
                    await asyncio.sleep(2)
                _report(backend, consumers, waits, lag, show)
                print("(a replay's clock stands at the time of each batch: its wait is 0)")
                await waits.aclose()
                _bare(backend, 50, show)
                for c in consumers:
                    await c.aclose()
    return 0
