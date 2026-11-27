"""The day run of WP-0.9: the backend on the live broker for 25 hours, measured.

    uv run python tools/live_day.py [--config PATH] [--until +1500] [--drops 10:30,14:30]
                                    [--removal-at 11:00] [--fake] [--port N]
    uv run python tools/live_day.py --summary FILE [--from 09:30] [--to 16:00]

It runs what ``python -m s3t`` runs, the socket, the backend and the server, so the app can be
opened while it runs. It runs 25 hours by default, because the backend runs across every session
and the weekend, not for a trading day (PRF-09 as worded on 2026-11-27): a run from a morning to the
next crosses every session of stocks and futures, the futures' daily break, the 24-hour life of the
stream's quote token, a refresh of the rates and a daily copy of the state file. Beside it, in the
same process, it measures what WP-0.9 asks (plan, sections 4 and 7):

- **Memory** (PRF-09): the peak resident memory (``s3t.system.process``) and the current one
  (``ps``), once a minute. The bound is ``[process] memory_bound_mb``.
- **The stream in regular hours:** events and batches per type, and how long each batch waited
  from its receipt to its take by a reader with the market state's queue rules
  (``tools/day_measures.py``). The overshoot of a 100 ms timer, as the lag of the event loop.
  The CPU seconds per minute of the process and of the event loop's thread, so that the headroom
  can be estimated for another machine (QA-08); the machine is in the first line.
- **IV per flush,** the stand-in for MKT-04 of R1: at each flush the options whose quote changed,
  inverted in one array call on the event loop, timed. Only the cost is measured.
- **The states,** once a minute, from the health record: the stream, the feed, the market state's
  lost batches, the REST client, the daily copy of the state file.
- **Forced drops** (BRK-02): at the times given, the tool closes the session's socket, as
  ``tools/stream_check.py`` does, and notes when the session is connected again, when the manager
  has sent everything again, and when the first data comes.
- **Removal through the manager:** once, the SPY quote leaves the demand for a minute, and the
  SPY quotes that still come after 2 s are counted.

**The demand** is the consumer ``measure`` in the class of views: Quote, Trade, Summary and
Profile of SPY and SPX, Quote, Trade and Summary of the front /ES future, and Quote and Greeks of
the options nearest the money: two SPY expirations of 150 strikes, one SPXW expiration of 200 and
the first /ES expiration of 50, from the day of the data on. The /ES options give the night data.
That is about 2,200 pairs. The tool refuses more than 2,500. A stated, read-only use: it places
no order and changes nothing (GEN-01); the subscription manager sends every subscription (SUB-02).

**Output** in ``<data_dir>/measurements/`` (git-ignored, GEN-07): ``day-<date>-<HHMM>.jsonl``
with a line at the start, one a minute, one per drop and removal, one at the end; and the log of
the run beside it, through the redaction filter. Times, counts, sizes and states only: no price,
no account (GEN-03).

Times are New York wall-clock times (``HH:MM`` on the day of the start, or ``YYYY-MM-DD HH:MM``)
or minutes after the start (``+N``). ``--fake``
runs the same on the synthetic market, starting in a regular session, with a state file of its
own in a temporary directory: to try the tool. ``--summary`` prints the median, the 95th
percentile and the maximum of each measure over the minutes between ``--from`` and ``--to``.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import math
import os
import resource
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from day_measures import IvTaker, LagReader, expirations_from, machine, option_set, summary
from s3t.__main__ import install_logging
from s3t.api.app import create_app
from s3t.api.server import listen, serve
from s3t.broker.interface import EventType, Sub
from s3t.config import Config, load_config
from s3t.secrets import install_redaction
from s3t.subs.feed import Priority
from s3t.system.backend import Backend, open_backend
from s3t.system.health import health
from s3t.system.process import MB, peak_rss_bytes

NY = ZoneInfo("America/New_York")
UNDERLYING_EVENTS = (EventType.QUOTE, EventType.TRADE, EventType.SUMMARY, EventType.PROFILE)
OPTION_EVENTS = (EventType.QUOTE, EventType.GREEKS)
MOST_PAIRS = 2_500
FAKE_START = "T15:00:00Z"  # 10:00 New York: the synthetic market moves in the regular session

LOG = logging.getLogger("live_day")


def at_time(text: str, start: datetime) -> datetime:
    """``HH:MM`` New York on the day of ``start``, ``YYYY-MM-DD HH:MM`` New York, or ``+N``
    minutes after ``start``."""
    if text.startswith("+"):
        return start + timedelta(minutes=float(text[1:]))
    if " " in text:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=NY).astimezone(UTC)
    hour, minute = (int(x) for x in text.split(":"))
    local = start.astimezone(NY).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local.astimezone(UTC)


class Day:
    def __init__(self, backend: Backend, args: argparse.Namespace, out: Path) -> None:
        self.backend, self.args, self.out = backend, args, out
        self.started = datetime.now(UTC)
        self.lines = out.open("a", encoding="utf-8")
        broker = backend.broker
        self.reader = LagReader(backend.feed, now=broker.data_time, watch="SPY")
        self.demand = backend.feed.consumer("measure", Priority.VIEWS)
        self.full: list[Sub] = []
        self.iv: IvTaker | None = None
        self.ticks: list[float] = []
        self.cpu = self._cpu()
        self.loop_cpu = time.thread_time()  # the event loop's thread: this is built on it

    def write(self, kind: str, **values: Any) -> None:
        now = datetime.now(UTC)
        line = {"kind": kind, "at": now.isoformat(), "ny": f"{now.astimezone(NY):%Y-%m-%d %H:%M}"}
        self.lines.write(json.dumps({**line, **values}) + "\n")
        self.lines.flush()

    @staticmethod
    def _cpu() -> float:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_utime + usage.ru_stime

    async def demand_set(self) -> dict[str, Any]:
        """Ask for the underlyings, find the spots, then ask for the options too."""
        backend = self.backend
        market = backend.market
        day = backend.broker.data_time().astimezone(NY).date()
        es_first = es_front = None
        if self.args.es_strikes:
            es = await backend.instruments.futures_chain("ES")
            listed = [e for e in es.expirations if e.expiration_date >= day]
            es_first = listed[0] if listed else None
            streams = {f.symbol: f.stream_symbol for f in es.futures}
            es_front = streams.get(es_first.underlying) if es_first else None
        spot_symbols = ["SPY", "SPX"] + ([es_front] if es_front else [])
        under = [Sub(s, e) for s in ("SPY", "SPX") for e in UNDERLYING_EVENTS]
        if es_front:
            under += [Sub(es_front, e) for e in UNDERLYING_EVENTS if e is not EventType.PROFILE]
        self.demand.set(under)
        spots: dict[str, float] = {}
        for _ in range(300):
            trades = market.column(EventType.TRADE, "price", spot_symbols)
            spots = {s: p for s, p in zip(spot_symbols, trades, strict=True) if math.isfinite(p)}
            if len(spots) == len(spot_symbols):
                break
            await asyncio.sleep(0.1)
        if "SPY" not in spots or "SPX" not in spots:
            raise RuntimeError(f"no trade of SPY and SPX within 30 s: {sorted(spots)}")
        spy = await backend.instruments.chain("SPY")
        spx = await backend.instruments.chain("SPX")
        picks = [
            (e, e.nearest(spots["SPY"], self.args.spy_strikes), "SPY")
            for e in expirations_from(spy, "SPY", day, self.args.spy_expirations)
        ]
        picks += [
            (e, e.nearest(spots["SPX"], self.args.spxw_strikes), "SPX")
            for e in expirations_from(spx, "SPXW", day, 1)
        ]
        if es_first is not None and es_front in spots:
            picks.append(
                (es_first, es_first.nearest(spots[es_front], self.args.es_strikes), es_front)
            )
        options = option_set(picks, backend.calendar)
        wanted = under + [Sub(s, e) for s in options.streams for e in OPTION_EVENTS]
        if len(wanted) > MOST_PAIRS:
            raise RuntimeError(f"{len(wanted)} pairs: more than the {MOST_PAIRS} of a stated check")
        self.full = wanted
        began = time.monotonic()
        self.demand.set(wanted)
        await asyncio.sleep(0.2)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(backend.feed.settled(), 120)
        self.iv = IvTaker(market, options, now=backend.broker.data_time, curve=self._curve)
        return {
            "machine": machine(),
            "pairs": len(wanted),
            "options": len(options),
            "expirations": [f"{e.root} {e.expiration_date}" for e, _, _ in picks],
            "sent_after_s": round(time.monotonic() - began, 2),
            "granted": backend.feed.status().granted,
        }

    def _curve(self) -> Any:
        return self.backend.rates.curve(self.backend.broker.data_time())

    async def ticker(self) -> None:
        """The lag of the event loop: how late a 100 ms sleep wakes."""
        while True:
            before = time.monotonic()
            await asyncio.sleep(0.1)
            self.ticks.append(time.monotonic() - before - 0.1)

    async def minutes(self) -> None:
        while True:
            await asyncio.sleep(60 - time.time() % 60)
            await self.minute()

    async def minute(self) -> None:
        cpu, loop_cpu = self._cpu(), time.thread_time()
        read = self.reader.samples.take()
        iv = self.iv.samples.take() if self.iv else None
        ticks, self.ticks = self.ticks, []
        doc = (await health(self.backend)).model_dump(mode="json")
        stream, use, market, rest = doc["stream"], doc["subscriptions"], doc["market"], doc["rest"]
        self.write(
            "minute",
            load=[round(x, 2) for x in os.getloadavg()],
            rss_mb=await current_rss_mb(),
            peak_rss_mb=round(peak_rss_bytes() / MB, 1),
            cpu_s=round(cpu - self.cpu, 3),
            loop_cpu_s=round(loop_cpu - self.loop_cpu, 3),
            loop_lag_ms=[round(t * 1000, 1) for t in ticks],
            events=dict(read.events),
            batches=dict(read.batches),
            reader_lag_ms=[round(t * 1000, 1) for t in read.lags],
            reader_lost=read.lost,
            iv_counts=iv.counts if iv else [],
            iv_ms=[round(s * 1000, 2) for s in iv.seconds] if iv else [],
            iv_cpu_ms=[round(s * 1000, 2) for s in iv.cpu] if iv else [],
            iv_failed=iv.failed if iv else 0,
            iv_fail_codes={str(k): v for k, v in iv.codes.items()} if iv else {},
            flushes=iv.flushes if iv else 0,
            stream={
                k: stream[k]
                for k in (
                    "phase",
                    "reconnects",
                    "stalls",
                    "subscribed",
                    "parse_errors",
                    "ignored_events",
                    "aggregation_period",
                )
                if k in stream
            },
            feed={
                k: use[k]
                for k in (
                    "resubscribing",
                    "granted",
                    "sent",
                    "pending",
                    "changes_last_minute",
                    "rate_errors",
                    "lost",
                    "failures",
                    "cap_refusals",
                )
            },
            market={k: market[k] for k in ("lost", "flushes", "failures", "values")},
            rest={k: rest[k] for k in ("auth", "requests_last_minute", "rate_limited", "renewals")},
            copies=doc["copies"],
            process=doc["process"],
        )
        self.cpu, self.loop_cpu = cpu, loop_cpu

    async def drop(self) -> None:
        """Close the session's socket, as ``tools/stream_check.py`` does, and time the return."""
        session = self.backend.feed._stream  # the session behind the feed
        conn = getattr(session, "_conn", None)
        if conn is None:
            self.write("drop", done=False, reason="no connection to close")
            return
        LOG.warning("live_day: forced drop of the stream now (look at the app)")
        dropped_at = datetime.now(UTC)
        at = time.monotonic()
        await conn.close()
        connected = await self.reader.first_mark("phase:connected", at, 120)
        filled = await self.reader.first_mark("fill:resubscribed", at, 180)
        data = await self.reader.first_mark("data", connected or at, 60)
        status = self.backend.feed.status()

        def since(t: float | None) -> float | None:
            return None if t is None else round(t - at, 2)

        self.write(
            "drop",
            done=True,
            dropped_at=dropped_at.isoformat(),
            connected_s=since(connected),
            resubscribed_s=since(filled),
            first_data_s=since(data),
            granted=status.granted,
            sent=status.sent,
            subscribed=status.stream.subscribed,
        )

    async def removal(self) -> None:
        """The SPY quote out of the demand for a minute, then in again."""
        quote = Sub("SPY", EventType.QUOTE)
        if quote not in self.full:
            return
        before = self.reader.watched
        self.demand.set([s for s in self.full if s != quote])
        await asyncio.sleep(2)
        after_2s = self.reader.watched
        await asyncio.sleep(58)
        gone = self.reader.watched - after_2s
        self.demand.set(self.full)
        await asyncio.sleep(10)
        back = self.reader.watched - after_2s - gone
        self.write(
            "removal",
            quotes_in_first_2s=after_2s - before,
            quotes_after_2s_for_58s=gone,
            quotes_in_10s_after_adding_again=back,
        )

    async def at(self, when: datetime, work: Any) -> None:
        await asyncio.sleep(max(0.0, (when - datetime.now(UTC)).total_seconds()))
        await work()

    async def run(self, stop: asyncio.Event) -> None:
        args, start = self.args, self.started
        tasks = [
            asyncio.create_task(self.reader.run(), name="measure-reader"),
            asyncio.create_task(self.ticker(), name="measure-ticker"),
        ]
        try:
            setup = await self.demand_set()
            assert self.iv is not None
            tasks.append(asyncio.create_task(self.iv.run(), name="measure-iv"))
            self.write(
                "start",
                source=self.backend.broker.source().kind,
                bound_mb=self.backend.config.process.memory_bound_mb,
                load=[round(x, 2) for x in os.getloadavg()],
                peak_rss_mb=round(peak_rss_bytes() / MB, 1),
                **setup,
            )
            tasks.append(asyncio.create_task(self.minutes(), name="measure-minutes"))
            for text in filter(None, args.drops.split(",")):
                when = at_time(text, start)
                tasks.append(asyncio.create_task(self.at(when, self.drop), name="measure-drop"))
            if args.removal_at:
                when = at_time(args.removal_at, start)
                tasks.append(asyncio.create_task(self.at(when, self.removal), name="measure-rm"))
            until = at_time(args.until, start)
            await asyncio.sleep(max(0.0, (until - datetime.now(UTC)).total_seconds()))
        except Exception as e:
            LOG.error("live_day: %s: %s", type(e).__name__, e)
            self.write("failed", error=f"{type(e).__name__}: {e}")
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def end(self) -> None:
        self.write(
            "end",
            peak_rss_mb=round(peak_rss_bytes() / MB, 1),
            load=[round(x, 2) for x in os.getloadavg()],
        )
        self.lines.close()


async def current_rss_mb() -> float | None:
    """The resident memory of this process now, from ``ps`` (KiB on macOS and Linux)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps", "-o", "rss=", "-p", str(os.getpid()), stdout=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        return round(int(out.strip()) / 1024, 1)
    except (OSError, ValueError):
        return None


async def run(config: Config, args: argparse.Namespace, out: Path) -> None:
    sock = listen(config.server)
    with contextlib.closing(sock):
        async with open_backend(config) as backend:
            day = Day(backend, args, out)
            stop = asyncio.Event()
            measuring = asyncio.create_task(day.run(stop), name="measure")
            await serve(create_app(backend), sock, stop=stop)
            stop.set()
            await measuring
            day.end()


def fake_config(config: Config, directory: Path, port: int | None) -> Config:
    """The configuration on the synthetic market, with a state file of its own."""
    start = datetime.fromisoformat(f"{datetime.now(UTC).date()}{FAKE_START}")
    broker = config.broker.model_copy(update={"source": "fake", "fake_start": start})
    paths = config.paths.model_copy(
        update={"state_file": directory / "state.sqlite", "backup_dir": directory / "backups"}
    )
    server = config.server.model_copy(update={"port": port or config.server.port})
    store = config.store.model_copy(update={"daily_copy": False})
    return config.model_copy(
        update={"broker": broker, "paths": paths, "server": server, "store": store}
    )


def print_summary(path: Path, start: str, end: str) -> int:
    """Median, 95th percentile and maximum of each measure over a stretch of the run. ``start``
    and ``end`` are times as for the run (``HH:MM`` on its first day, ``YYYY-MM-DD HH:MM``, or
    ``+N`` minutes after its first line); empty means its beginning or its end."""
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    first = datetime.fromisoformat(lines[0]["at"])
    since = at_time(start, first) if start else first
    until = at_time(end, first) if end else datetime.max.replace(tzinfo=UTC)
    minutes = [
        x
        for x in lines
        if x["kind"] == "minute" and since <= datetime.fromisoformat(x["at"]) < until
    ]
    for line in lines:
        if line["kind"] in ("start", "drop", "removal", "end", "failed"):
            print(json.dumps(line))
    if not minutes:
        print(f"no minute between {start} and {end} New York")
        return 1
    print(f"\n{len(minutes)} minutes from {minutes[0]['ny']} to {minutes[-1]['ny']} New York")

    def show(name: str, values: list[float], unit: str = "") -> None:
        s = summary(values)
        if s["n"]:
            print(
                f"{name:34} median {s['median']:>10.2f}  p95 {s['p95']:>10.2f}  "
                f"max {s['max']:>10.2f} {unit}  (n {s['n']})"
            )

    def flat(key: str) -> list[float]:
        return [v for m in minutes for v in m[key]]

    events: Counter[str] = Counter()
    for m in minutes:
        events.update(m["events"])
    for kind in sorted(events):
        show(f"events/s {kind}", [m["events"].get(kind, 0) / 60 for m in minutes])
    show("events/s all", [sum(m["events"].values()) / 60 for m in minutes])
    show("batches/s all", [sum(m["batches"].values()) / 60 for m in minutes])
    show("reader lag per batch", flat("reader_lag_ms"), "ms")
    show("event loop lag (100 ms timer)", flat("loop_lag_ms"), "ms")
    show("IV contracts per flush", flat("iv_counts"))
    show("IV time per flush", flat("iv_ms"), "ms")
    show("IV CPU per flush", flat("iv_cpu_ms") if "iv_cpu_ms" in minutes[0] else [], "ms")
    per_contract = [
        c * 1000 / n
        for m in minutes
        for c, n in zip(m.get("iv_cpu_ms", []), m["iv_counts"], strict=False)
        if n
    ]
    show("IV CPU per contract", per_contract, "us")
    show("CPU of the process, share of a core", [m["cpu_s"] / 60 * 100 for m in minutes], "%")
    loop = [m["loop_cpu_s"] / 60 * 100 for m in minutes if "loop_cpu_s" in m]
    show("CPU of the event loop's thread", loop, "%")
    per_event = [
        m["cpu_s"] * 1e6 / sum(m["events"].values()) for m in minutes if sum(m["events"].values())
    ]
    show("CPU of the process per event", per_event, "us")
    if per_event:
        median = summary(per_event)["median"]
        print(
            f"headroom, computed: at the median CPU per event one core takes about "
            f"{1e6 / median:,.0f} events a second, all work of the process included"
        )
    if loop:
        share = summary(loop)["median"]
        if share:
            print(
                f"headroom, computed: the event loop's thread used {share:.2f}% of a core at "
                f"the median, about {100 / share:,.0f} times less than it can"
            )
    rss = [m["rss_mb"] for m in minutes if m["rss_mb"] is not None]
    show("resident memory", rss, "MB")
    print(
        f"peak memory: first {minutes[0]['peak_rss_mb']} MB, last {minutes[-1]['peak_rss_mb']} MB"
    )
    loads = [m["load"][0] for m in minutes]
    show("load average, 1 minute", loads)
    last = minutes[-1]
    print(f"stream at the end: {last['stream']}")
    print(f"feed at the end: {last['feed']}")
    print(
        f"market at the end: {last['market']['lost']} lost, {last['market']['failures']} failures"
    )
    print(f"rest at the end: {last['rest']}")
    print(
        f"reader lost: {sum(m['reader_lost'] for m in minutes)}; IV failed: "
        f"{sum(m['iv_failed'] for m in minutes)} of {sum(sum(m['iv_counts']) for m in minutes)}"
    )
    codes: Counter[str] = Counter()
    for m in minutes:
        codes.update(m.get("iv_fail_codes", {}))
    print(f"IV failures by reason code (s3t.pricing.black76): {dict(sorted(codes.items()))}")
    print(f"copies at the end: {last['copies']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="The day run of WP-0.9.")
    parser.add_argument("--config", help="the configuration file (default: the usual search)")
    parser.add_argument(
        "--until", default="+1500", help="HH:MM or 'YYYY-MM-DD HH:MM' New York, or +N minutes"
    )
    parser.add_argument("--drops", default="10:30,14:30", help="times of forced drops, or ''")
    parser.add_argument("--removal-at", default="11:00", help="time of the removal, or ''")
    parser.add_argument("--spy-expirations", type=int, default=2)
    parser.add_argument("--spy-strikes", type=int, default=150)
    parser.add_argument("--spxw-strikes", type=int, default=200)
    parser.add_argument("--es-strikes", type=int, default=50, help="0 leaves /ES out")
    parser.add_argument("--fake", action="store_true", help="the synthetic market, to try it")
    parser.add_argument("--port", type=int, help="another port than the configuration's")
    parser.add_argument("--summary", type=Path, metavar="FILE", help="summarise a run instead")
    parser.add_argument("--from", dest="start", default="", help="with --summary: a time")
    parser.add_argument("--to", dest="end", default="", help="with --summary: a time")
    args = parser.parse_args()
    if args.summary:
        return print_summary(args.summary, args.start, args.end)

    install_logging()
    config = load_config(args.config)
    stamp = datetime.now(UTC).astimezone(NY)
    directory = config.paths.data_dir / "measurements"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"day-{stamp:%Y%m%d-%H%M}{'-fake' if args.fake else ''}"
    handler = logging.FileHandler(directory / f"{name}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    install_redaction(handler)
    logging.getLogger().addHandler(handler)
    out = directory / f"{name}.jsonl"
    print(f"writing {out}", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="s3t-day-") as temporary:
        if args.fake:
            config = fake_config(config, Path(temporary), args.port)
        elif args.port:
            config = config.model_copy(
                update={"server": config.server.model_copy(update={"port": args.port})}
            )
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run(config, args, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
