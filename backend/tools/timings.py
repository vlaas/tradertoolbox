"""Timings for an idle machine (QA-08): the calendar, the chains, the reader (WP-0.9) and the
surface fit (WP-1.1, PRF-05).

    uv run python tools/timings.py calendar [--runs 5]
    uv run python tools/timings.py chains [--runs 20] [--config PATH]
    uv run python tools/timings.py reader NAME [--speeds 10,30,100,300] [--config PATH]
    uv run python tools/timings.py surface [--symbols SPY,SPX,AAPL] [--runs 20] [--config PATH]

Each prints the machine, then the median, the 95th percentile and the maximum, with the load
average before and after, for ``docs/measurements.md``. Each timing has its CPU time beside its
wall time (QA-08, decided 2026-11-27), so that the headroom can be judged on another machine.
A timing on a loaded machine is not a measurement: look at the load first.

- ``calendar``: the import of the exchange calendar and the build of ``load_calendar``, each in a
  fresh process, with the growth of the peak resident memory (plan section 8, from WP-0.6). No
  broker, no network.
- ``chains``: one live read each of the SPY and SPX option chains (three requests with the token),
  then the parse of each document timed, against the 50 ms of PRF-05: the parse runs on the
  event loop (plan section 8, from WP-0.5). A stated, read-only use.
- ``reader NAME``: a local recording (``tools/capture.py``) replayed at multiples of its pace,
  through the feed, the market state and the IV taker of the day run (``tools/day_measures.py``).
  Per multiple: the events a second, whether the market state's reader lost batches (it did not
  keep up), the CPU share, and the IV per flush. The highest multiple without a loss bounds the
  capacity of the path, without the DXLink decoding, which a replay does not do (plan section 8,
  from WP-0.4). A replay as fast as the loop runs measures nothing: the replay outruns every
  reader and is over before the first flush.
- ``surface``: the surface fit in the process pool, on the fake broker: a fit per underlying and
  per slice, the steps on the event loop, and the lag of a 10 ms timer while fits run
  (``tools/surface_timings.py`` says what each figure is). No broker and no network. Without
  ``--config`` the settings are the defaults; with it, its ``[numerics]`` and ``[surface]``.
"""

import argparse
import asyncio
import json
import os
import resource
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

import surface_timings
from day_measures import IvTaker, machine, option_set, summary
from s3t.broker.interface import EventBatch, EventType, StreamState, Sub
from s3t.broker.replay import ReplayBroker
from s3t.broker.tastytrade import instruments as parse
from s3t.broker.tastytrade.rest import TastytradeRest
from s3t.config import Config, load_config
from s3t.market.state import MarketState
from s3t.pricing.rates import RateCurve
from s3t.refdata.calendar import load_calendar
from s3t.secrets import load_broker_secrets
from s3t.subs.feed import Priority
from s3t.subs.local import LocalFeed

CALENDAR_PROBE = """
import json, resource, sys, time
from datetime import date
def peak():
    p = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return p if sys.platform == "darwin" else p * 1024
before = peak()
t0, c0 = time.perf_counter(), time.process_time()
from s3t.refdata.calendar import load_calendar
t1, c1 = time.perf_counter(), time.process_time()
load_calendar(date.fromisoformat(sys.argv[1]))
t2, c2 = time.perf_counter(), time.process_time()
print(json.dumps({
    "import_s": t1 - t0, "build_s": t2 - t1, "import_cpu_s": c1 - c0, "build_cpu_s": c2 - c1,
    "before": before, "after": peak(),
}))
"""


def load() -> str:
    return ", ".join(f"{x:.2f}" for x in os.getloadavg())


def print_machine() -> None:
    print(f"machine: {json.dumps(machine())}")


def show(name: str, values: list[float], unit: str) -> None:
    s = summary(values)
    print(
        f"{name:40} median {s['median']:9.3f}  p95 {s['p95']:9.3f}  max {s['max']:9.3f} {unit}"
        f"  (n {s['n']})"
    )


def calendar(runs: int) -> int:
    print_machine()
    print(f"load before: {load()}")
    found: list[dict[str, Any]] = []
    for _ in range(runs):
        out = subprocess.run(
            [sys.executable, "-c", CALENDAR_PROBE, datetime.now(UTC).date().isoformat()],
            check=True,
            capture_output=True,
            text=True,
        )
        found.append(json.loads(out.stdout.strip().splitlines()[-1]))
    print(f"load after: {load()}")
    show("import of s3t.refdata.calendar", [f["import_s"] * 1000 for f in found], "ms")
    show("  its CPU", [f["import_cpu_s"] * 1000 for f in found], "ms")
    show("load_calendar, library import in", [f["build_s"] * 1000 for f in found], "ms")
    show("  its CPU", [f["build_cpu_s"] * 1000 for f in found], "ms")
    show("peak memory before", [f["before"] / 2**20 for f in found], "MB")
    show("peak memory growth", [(f["after"] - f["before"]) / 2**20 for f in found], "MB")
    return 0


async def chains(config: Config, runs: int) -> int:
    client = TastytradeRest(load_broker_secrets(config.paths.env_file), rate=1.0, burst=1)
    try:
        docs = {s: await client._read("option_chain", symbol=s) for s in ("SPY", "SPX")}
    finally:
        await client.aclose()
    print_machine()
    print(f"load before: {load()}")
    for symbol, doc in docs.items():
        times: list[float] = []
        cpu: list[float] = []
        contracts = 0
        for _ in range(runs):
            started, used = time.perf_counter(), time.process_time()
            chain = parse.parse_option_chain(doc, symbol)
            times.append((time.perf_counter() - started) * 1000)
            cpu.append((time.process_time() - used) * 1000)
            contracts = sum(
                sum(c is not None for c in (*e.call_streams, *e.put_streams))
                for e in chain.expirations
            )
        show(f"parse of {symbol} ({contracts} contracts)", times, "ms")
        show("  its CPU", cpu, "ms")
    print(f"load after: {load()}")
    return 0


def recorded_pairs(broker: ReplayBroker) -> list[Sub]:
    """The pairs that have events in a recording, candles left out."""
    names: dict[str, list[str]] = {}
    pairs: dict[tuple[str, str], None] = {}
    for line in broker.stream_lines():
        if line.get("k") == "f":
            names[line["e"]] = line["f"]
        elif line.get("k") == "b" and line["e"] != EventType.CANDLE.value:
            symbols = line["c"][names[line["e"]].index("symbol")]
            for symbol in symbols:
                pairs[(symbol, line["e"])] = None
    return [Sub(symbol, EventType(event)) for symbol, event in pairs]


async def replay_once(config: Config, name: str, speed: float) -> dict[str, float]:
    """One replay at a multiple of the recorded pace, through the feed, the market state and
    the IV taker. The market state's reader has its usual queue (2,000 batches); what it
    loses says that the path did not keep up."""
    broker = ReplayBroker(config.paths.recordings_dir / name, speed=speed, paused=True)
    pairs = recorded_pairs(broker)
    feed = LocalFeed(broker, config.subscriptions)
    market = MarketState(feed, now=broker.data_time, period=config.stream.aggregation_period)
    counting = feed.reader("timings", maxsize=1_000_000)
    demand = feed.consumer("timings", Priority.RECORDER)
    ended = asyncio.Event()
    events = 0
    recorded: list[Any] = []

    async def count() -> None:
        nonlocal events
        async for item in counting:
            if isinstance(item, EventBatch):
                events += item.count
                recorded.append(item.received_at)
            elif isinstance(item, StreamState) and item.phase == "disconnected":
                ended.set()

    feed.start()
    market.start()
    counter = asyncio.create_task(count())
    options = []
    quoted = {p.symbol for p in pairs if p.event is EventType.QUOTE}
    for underlying in sorted({p.symbol for p in pairs if not p.symbol.startswith(".")}):
        try:
            chain = await broker.option_chain(underlying)
        except Exception:
            continue
        chosen: dict[int, tuple[Any, set[int]]] = {}
        for e in chain.expirations:
            for i in range(len(e.strikes)):
                if e.call_streams[i] in quoted or e.put_streams[i] in quoted:
                    chosen.setdefault(id(e), (e, set()))[1].add(i)
        options += [(e, sorted(ix), underlying) for e, ix in chosen.values()]
    calendar = load_calendar(broker.data_time().date())
    taker = IvTaker(
        market,
        option_set(options, calendar),
        now=broker.data_time,
        curve=lambda: RateCurve.flat(config.rates.flat_rate),
    )
    iv = asyncio.create_task(taker.run())
    demand.set(pairs)
    await asyncio.wait_for(feed.settled(), 60)
    cpu0 = sum(resource.getrusage(resource.RUSAGE_SELF)[:2])
    started = time.perf_counter()
    broker.resume()
    await asyncio.wait_for(ended.wait(), 3600)
    await asyncio.sleep(2 * config.stream.aggregation_period)  # the last flush
    wall = time.perf_counter() - started
    cpu = sum(resource.getrusage(resource.RUSAGE_SELF)[:2]) - cpu0
    span = (max(recorded) - min(recorded)).total_seconds() if recorded else 0.0
    lost = market.status().lost
    for task in (counter, iv):
        task.cancel()
    await asyncio.gather(counter, iv, return_exceptions=True)
    await market.aclose()
    await feed.aclose()
    await broker.aclose()
    samples = taker.samples
    iv_ms = summary([x * 1000 for x in samples.seconds])
    per_flush = summary([float(c) for c in samples.counts])
    return {
        "speed": speed,
        "pairs": len(pairs),
        "options": len(taker._options),
        "events": events,
        "market_lost": lost,
        "wall_s": wall,
        "cpu_s": cpu,
        "recorded_s": span,
        "events_per_s": events / wall if wall else 0.0,
        "iv_flushes": len(samples.seconds),
        "iv_ms_median": iv_ms.get("median", 0.0),
        "iv_ms_max": iv_ms.get("max", 0.0),
        "iv_per_flush_median": per_flush.get("median", 0.0),
    }


async def reader(config: Config, name: str, speeds: list[float]) -> int:
    print_machine()
    print(f"load before: {load()}")
    found = [await replay_once(config, name, speed) for speed in speeds]
    print(f"load after: {load()}")
    one = found[0]
    print(
        f"recording {name}: {one['pairs']} pairs, {one['options']} options with IV, "
        f"{one['events']:.0f} events over {one['recorded_s']:.0f} s recorded "
        f"({one['events'] / max(one['recorded_s'], 1):.0f} a second as recorded)"
    )
    print(
        f"{'speed':>6} {'events/s':>10} {'wall s':>8} {'expected s':>10} {'CPU %':>6} "
        f"{'lost':>6} {'flushes':>8} {'IV/flush':>9} {'IV ms med':>10} {'IV ms max':>10} "
        f"{'CPU us/event':>12}"
    )
    for f in found:
        print(
            f"{f['speed']:>6g} {f['events_per_s']:>10.0f} {f['wall_s']:>8.1f} "
            f"{f['recorded_s'] / f['speed']:>10.1f} {f['cpu_s'] / f['wall_s'] * 100:>6.0f} "
            f"{f['market_lost']:>6.0f} {f['iv_flushes']:>8.0f} {f['iv_per_flush_median']:>9.0f} "
            f"{f['iv_ms_median']:>10.2f} {f['iv_ms_max']:>10.2f} "
            f"{f['cpu_s'] * 1e6 / max(f['events'], 1):>12.1f}"
        )
    print(
        "CPU per event is all the work of the process, the replay's own reading included, so it "
        "is an upper bound for the path"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("what", choices=("calendar", "chains", "reader", "surface"))
    parser.add_argument("name", nargs="?", help="the recording, for reader")
    parser.add_argument("--runs", type=int, help="for calendar, chains and surface")
    parser.add_argument("--symbols", default="SPY,SPX,AAPL", help="for surface: the underlyings")
    parser.add_argument(
        "--speeds", default="10,30,100,300", help="for reader: multiples of the pace"
    )
    parser.add_argument("--config", help="the configuration file (default: the usual search)")
    args = parser.parse_args()
    if args.what == "calendar":
        return calendar(args.runs or 5)
    if args.what == "surface":
        own = load_config(args.config) if args.config else None
        symbols = [s for s in args.symbols.split(",") if s]
        print_machine()
        print(f"load before: {load()}")
        try:
            return asyncio.run(surface_timings.surface(own, symbols, args.runs or 20, show))
        finally:
            print(f"load after: {load()}")
    config = load_config(args.config)
    if args.what == "chains":
        return asyncio.run(chains(config, args.runs or 20))
    if not args.name:
        parser.error("reader needs the name of a recording")
    speeds = [float(x) for x in args.speeds.split(",")]
    return asyncio.run(reader(config, args.name, speeds))


if __name__ == "__main__":
    raise SystemExit(main())
