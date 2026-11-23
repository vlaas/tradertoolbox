"""Live read-only check of the streaming session against Tastytrade (BRK-02; WP-0.4).

    uv run python tools/stream_check.py [--config PATH]

A stated check: one session, about ten subscriptions, about 100 seconds. It reads the SPY
chain once (two REST requests with the token renewal, three with the quote token). Then it:

1. subscribes Quote, Trade, Summary and Profile of SPY, Quote and Trade of SPX, Quote and
   Greeks of one SPY option near the money, and the daily candles of SPY from ten days ago;
2. after 30 s removes the SPY quote and counts whether quotes still come;
3. after 45 s closes its own socket and watches the session come back;
4. prints what it saw: the granted aggregation period, the field lists the server set, the
   counts per event type, the candle snapshot (order and flags), the phases, the gap, and
   the key names of the quote-token response.

Nothing is written to disk and no value of the market is kept. Logging goes through the
redaction filter. It places no order and changes nothing (GEN-01).
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

from s3t.broker.interface import (
    BrokerError,
    EventBatch,
    EventType,
    Gap,
    StreamItem,
    StreamNotice,
    StreamState,
    Sub,
)
from s3t.broker.tastytrade.broker import from_config
from s3t.broker.tastytrade.chain import near_the_money
from s3t.broker.tastytrade.dxlink import EVENT_FIELDS
from s3t.config import load_config
from s3t.secrets import install_redaction


async def run(config_path: str | None) -> int:
    broker = from_config(load_config(config_path))
    started = datetime.now(UTC)
    items: list[tuple[float, StreamItem]] = []

    def sink(item: StreamItem) -> None:
        items.append(((datetime.now(UTC) - started).total_seconds(), item))

    def rows(event: EventType, symbol: str, since: float = 0.0) -> list[dict]:
        return [
            row
            for at, item in items
            if isinstance(item, EventBatch) and item.event is event and at >= since
            for row in item.rows()
            if row["eventSymbol"] == symbol
        ]

    stream = broker.stream(sink)
    try:
        chain = await broker.option_chain("SPY")
        stream.start()
        spy = [Sub("SPY", e) for e in (EventType.QUOTE, EventType.TRADE, EventType.SUMMARY)]
        candle = Sub("SPY{=d}", EventType.CANDLE, from_time=started - timedelta(days=10))
        first = [*spy, Sub("SPY", EventType.PROFILE), candle]
        first += [Sub("SPX", EventType.QUOTE), Sub("SPX", EventType.TRADE)]
        stream.subscribe(first)
        await asyncio.wait_for(stream.synced(), 60)
        for _ in range(100):
            if rows(EventType.TRADE, "SPY") or rows(EventType.QUOTE, "SPY"):
                break
            await asyncio.sleep(0.1)
        trades, quotes = rows(EventType.TRADE, "SPY"), rows(EventType.QUOTE, "SPY")
        spot = trades[-1]["price"] if trades else quotes[-1]["bidPrice"] if quotes else None
        option = None
        if isinstance(spot, int | float):
            found = near_the_money(chain, float(spot), expirations=1, strikes=1, min_days=7)
            option = found[0] if found else None
        if option:
            stream.subscribe([Sub(option, EventType.QUOTE), Sub(option, EventType.GREEKS)])
        await asyncio.sleep(30)

        removed_at = (datetime.now(UTC) - started).total_seconds()
        stream.unsubscribe([Sub("SPY", EventType.QUOTE)])
        await asyncio.wait_for(stream.synced(), 30)
        await asyncio.sleep(15)
        after_remove = len(rows(EventType.QUOTE, "SPY", removed_at + 2.0))

        dropped_at = (datetime.now(UTC) - started).total_seconds()
        conn = stream._conn
        if conn is not None:
            await conn.close()  # our own socket: the forced drop
        await asyncio.sleep(40)
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
    counts: Counter[tuple[str, str]] = Counter()
    for _, item in items:
        if isinstance(item, EventBatch):
            for row in item.rows():
                kind = "option" if row["eventSymbol"] == option else row["eventSymbol"]
                counts[(item.event.value, kind)] += 1
    for (event, symbol), n in sorted(counts.items()):
        print(f"events {event:8} {symbol:10} {n}")
    snapshot = next(
        (i for _, i in items if isinstance(i, EventBatch) and i.event is EventType.CANDLE), None
    )
    if snapshot is not None:
        bars = list(snapshot.rows())
        times = [b["time"] for b in bars]
        order = "newest first" if times == sorted(times, reverse=True) else "NOT newest first"
        flags = [b["eventFlags"] for b in bars]
        print(f"first candle message: {len(bars)} bars, {order}, flags {flags}")
    quote = rows(EventType.QUOTE, "SPX")
    if quote:
        sizes = {type(quote[-1][k]).__name__ for k in ("bidSize", "askSize")}
        print(f"SPX quote sizes are of type: {sorted(sizes)}")
    print(f"SPY quotes later than 2 s after the removal: {after_remove} (0 means it works)")
    print(f"removal at {removed_at:.1f} s, forced drop at {dropped_at:.1f} s")
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
        f"reconnects {status.reconnects}, stalls {status.stalls}, held {status.held}, "
        f"rate errors {status.rate_errors}, parse errors {stream.parse_errors}"
    )
    ok = status.phase == "connected" and status.reconnects == 1 and bool(after_drop)
    print("OK" if ok else "NOT AS EXPECTED")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Live read-only check of the streaming session.")
    parser.add_argument("--config", help="configuration file (default: S3T_CONFIG or config/)")
    args = parser.parse_args()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    install_redaction(handler)
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(run(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
