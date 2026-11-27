"""Live read-only check of the streaming session against Tastytrade (BRK-02; WP-0.4).

    uv run python tools/stream_check.py [--config PATH] [--candles DAY]

A stated check: one session, about ten subscriptions, about 100 seconds. It reads the SPY
chain once (two REST requests with the token renewal, three with the quote token). Then it:

1. waits for the session to say ``connected`` (all six channels open), and subscribes Quote,
   Trade, Summary and Profile of SPY, Quote and Trade of SPX, Quote and Greeks of one SPY
   option near the money, and the daily candles of SPY from ten days ago;
2. after 30 s removes the SPY quote and counts whether quotes still come;
3. after 45 s closes its own socket, watches the session come back open and empty, and
   sends its subscriptions again itself, as the subscription manager does in the backend;
4. prints what it saw: the granted aggregation period, the field lists the server set (and
   which of them came before the first subscription), the counts per event type, the candle
   snapshot (order and marks), the phases, the gap, and the key names of the quote-token
   response.

With ``--candles DAY`` it does something else: it asks for the 15-minute candles of SPY and of
the front /ES future from that day on (two candle subscriptions, about 20 seconds) and prints
the times of the bars of that day in New York, as runs of consecutive bars. It shows where the
broker's sessions began and ended on an early close (WP-0.9; plan section 8, from WP-0.6). One
REST request more: the /ES futures chain. Times only, no prices.

It talks to the session directly, without the feed, because it checks the session alone.
Nothing is written to disk and no value of the market is kept or printed. Logging goes
through the redaction filter. It places no order and changes nothing (GEN-01).
"""

import argparse
import asyncio
import logging
import math
import sys
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from s3t.broker.interface import (
    BrokerError,
    Candle,
    CandlePeriod,
    EventBatch,
    EventType,
    Gap,
    StreamItem,
    StreamNotice,
    StreamState,
    Sub,
)
from s3t.broker.tastytrade.broker import from_config
from s3t.broker.tastytrade.convert import EVENT_FIELDS
from s3t.config import load_config
from s3t.secrets import install_redaction


async def run(config_path: str | None) -> int:
    broker = from_config(load_config(config_path))
    started = datetime.now(UTC)
    items: list[tuple[float, StreamItem]] = []

    def elapsed() -> float:
        return (datetime.now(UTC) - started).total_seconds()

    def sink(item: StreamItem) -> None:
        items.append((elapsed(), item))

    def rows(event: EventType, symbol: str, since: float = 0.0) -> list[Any]:
        return [
            row
            for at, item in items
            if isinstance(item, EventBatch) and item.event is event and at >= since
            for row in item.rows()
            if row.symbol == symbol
        ]

    async def connected(times: int) -> float:
        """Wait until the session has said ``connected`` this many times."""
        for _ in range(600):
            seen = [at for at, i in items if isinstance(i, StreamState) and i.phase == "connected"]
            if len(seen) >= times:
                return seen[times - 1]
            await asyncio.sleep(0.05)
        raise TimeoutError(f"not connected {times} time(s) within 30 s")

    stream = broker.stream(sink)
    try:
        chain = await broker.option_chain("SPY")
        stream.start()
        await connected(1)
        before_subscribing = sorted(e.value for e in stream._fields)
        spy = [Sub("SPY", e) for e in (EventType.QUOTE, EventType.TRADE, EventType.SUMMARY)]
        day = CandlePeriod(1, "day")
        candle = Sub("SPY", EventType.CANDLE, day, started - timedelta(days=10))
        held = [*spy, Sub("SPY", EventType.PROFILE), candle]
        held += [Sub("SPX", EventType.QUOTE), Sub("SPX", EventType.TRADE)]
        await stream.subscribe(held)
        for _ in range(100):
            if rows(EventType.TRADE, "SPY") or rows(EventType.QUOTE, "SPY"):
                break
            await asyncio.sleep(0.1)
        trades, quotes = rows(EventType.TRADE, "SPY"), rows(EventType.QUOTE, "SPY")
        spot = trades[-1].price if trades else quotes[-1].bid if quotes else math.nan
        option = None
        if math.isfinite(spot):
            week = broker.data_time().date() + timedelta(days=7)
            found = chain.near_the_money(spot, expirations=1, strikes=1, not_before=week)
            option = found[0] if found else None
        if option:
            more = [Sub(option, EventType.QUOTE), Sub(option, EventType.GREEKS)]
            await stream.subscribe(more)
            held += more
        await asyncio.sleep(30)

        removed_at = elapsed()
        await stream.unsubscribe([Sub("SPY", EventType.QUOTE)])
        held.remove(Sub("SPY", EventType.QUOTE))
        await asyncio.sleep(15)
        after_remove = len(rows(EventType.QUOTE, "SPY", removed_at + 2.0))

        dropped_at = elapsed()
        conn = stream._conn
        if conn is not None:
            await conn.close()  # our own socket: the forced drop
        back_at = await connected(2)
        empty = stream.status().subscribed == 0
        await stream.subscribe(held)  # the session sends nothing again by itself
        sent_again_at = elapsed()
        await asyncio.sleep(max(0.0, dropped_at + 40 - elapsed()))
        status = stream.status()
    except (BrokerError, TimeoutError) as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    finally:
        await broker.aclose()

    print(f"quote token response keys: {', '.join(broker.quote_token_keys)}")
    print(f"granted aggregation period: {status.aggregation_period}")
    for event, fields in stream._fields.items():
        same = "as asked" if fields == EVENT_FIELDS[event] else f"SERVER'S OWN: {fields}"
        print(f"fields of {event.value}: {same}")
    print(
        "field lists from the server before the first subscription: "
        f"{', '.join(before_subscribing) or 'none'}"
    )
    silent = [e.value for e in EventType if e not in stream._fields]
    print(f"event types without a field list from the server: {silent or 'none'}")
    counts: Counter[tuple[str, str]] = Counter()
    for _, item in items:
        if isinstance(item, EventBatch):
            for row in item.rows():
                kind = "option" if row.symbol == option else row.symbol
                counts[(item.event.value, kind)] += 1
    for (event, symbol), n in sorted(counts.items()):
        print(f"events {event:8} {symbol:10} {n}")
    snapshot = next(
        (i for _, i in items if isinstance(i, EventBatch) and i.event is EventType.CANDLE), None
    )
    if snapshot is not None:
        bars = [b for b in snapshot.rows() if isinstance(b, Candle)]
        times = [b.time for b in bars]
        order = "newest first" if times == sorted(times, reverse=True) else "NOT newest first"
        marks = [("end" if b.snapshot_end else "") + ("removed" if b.removed else "") for b in bars]
        last = bars[-1]
        print(f"first candle message: {len(bars)} events, {order}, period {last.period.text()}")
        print(f"  marks, in order: {['-' if not m else m for m in marks]}")
        at_start = (
            abs((last.time - candle.from_time).total_seconds()) < 1 if candle.from_time else False
        )
        print(
            f"  the last event: has prices: {math.isfinite(last.close)}; "
            f"its time is the start asked for: {at_start}"
        )
    quote = rows(EventType.QUOTE, "SPX")
    if quote:
        sizes = "NaN" if math.isnan(quote[-1].bid_size) else "numbers"
        print(f"SPX quote sizes after conversion: {sizes} (floats)")
    print(f"SPY quotes later than 2 s after the removal: {after_remove} (0 means it works)")
    print(f"removal at {removed_at:.1f} s, forced drop at {dropped_at:.1f} s")
    print(
        f"after the drop: connected again {back_at - dropped_at:.2f} s later, "
        f"empty: {empty}; {len(held)} subscriptions sent again "
        f"{sent_again_at - dropped_at:.2f} s after the drop"
    )
    for at, item in items:
        if isinstance(item, StreamState):
            print(f"{at:7.2f} s  phase {item.phase}" + (f" ({item.reason})" if item.reason else ""))
        elif isinstance(item, Gap):
            lost = (item.until - item.since).total_seconds()
            print(f"{at:7.2f} s  gap of {lost:.2f} s after item {item.last_seq} ({item.reason})")
        elif isinstance(item, StreamNotice):
            print(f"{at:7.2f} s  notice {item.kind}: {item.message}")
    after_drop = [at for at, i in items if isinstance(i, EventBatch) and at > dropped_at]
    if after_drop:
        print(f"first data after the drop: {after_drop[0] - dropped_at:.2f} s later")
    print(
        f"reconnects {status.reconnects}, stalls {status.stalls}, "
        f"subscribed {status.subscribed}, candles {status.candles}, "
        f"parse errors {stream.parse_errors}, ignored events {stream.ignored_events}"
    )
    ok = status.phase == "connected" and status.reconnects == 1 and empty and bool(after_drop)
    ok = ok and status.subscribed == len(held) and stream.parse_errors == 0
    print("OK" if ok else "NOT AS EXPECTED")
    return 0 if ok else 1


NY = ZoneInfo("America/New_York")
QUARTER = timedelta(minutes=15)


def runs(times: list[datetime]) -> list[str]:
    """Consecutive 15-minute bars as runs: ``04:00-16:45 (52 bars)``, in New York time."""
    found: list[str] = []
    ordered = sorted(times)
    start = 0
    for i in range(1, len(ordered) + 1):
        if i == len(ordered) or ordered[i] - ordered[i - 1] != QUARTER:
            first, last = ordered[start].astimezone(NY), ordered[i - 1].astimezone(NY)
            found.append(f"{first:%H:%M}-{last:%H:%M} ({i - start} bars)")
            start = i
    return found


async def candles(config_path: str | None, day: date) -> int:
    """The 15-minute bars of SPY and the front /ES on one day, as runs of New York times."""
    broker = from_config(load_config(config_path))
    items: list[StreamItem] = []
    stream = broker.stream(items.append)
    try:
        chain = await broker.futures_option_chain("ES")
        front = next((f for f in chain.futures if f.active_month), chain.futures[0])
        stream.start()
        for _ in range(600):
            if any(isinstance(i, StreamState) and i.phase == "connected" for i in items):
                break
            await asyncio.sleep(0.05)
        else:
            raise TimeoutError("not connected within 30 s")
        start = datetime.combine(day, time(0, 0), NY)
        period = CandlePeriod(15, "minute")
        symbols = {"SPY": "SPY", front.stream_symbol: f"/ES front ({front.symbol})"}
        await stream.subscribe([Sub(s, EventType.CANDLE, period, start) for s in symbols])
        await asyncio.sleep(20)
    except (BrokerError, TimeoutError) as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    finally:
        await broker.aclose()
    bars: dict[str, set[datetime]] = {s: set() for s in symbols}
    for item in items:
        if isinstance(item, EventBatch) and item.event is EventType.CANDLE:
            for bar in item.rows():
                if isinstance(bar, Candle) and not bar.removed and bar.symbol in bars:
                    if bar.time.astimezone(NY).date() == day:
                        bars[bar.symbol].add(bar.time)
    print(f"15-minute bars on {day}, New York time, by the start of each bar:")
    for symbol, label in symbols.items():
        print(f"  {label}: {', '.join(runs(list(bars[symbol]))) or 'none'}")
    return 0 if all(bars.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Live read-only check of the streaming session.")
    parser.add_argument("--config", help="configuration file (default: S3T_CONFIG or config/)")
    parser.add_argument(
        "--candles", type=date.fromisoformat, metavar="DAY", help="the bars of a day instead"
    )
    args = parser.parse_args()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    install_redaction(handler)
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.candles is not None:
        return asyncio.run(candles(args.config, args.candles))
    return asyncio.run(run(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
