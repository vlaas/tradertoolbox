"""Capture tool: record a broker session into a local recording (implementation plan 3.2).

    uv run python tools/capture.py NAME [--symbols SPY,SPX] [--minutes 5]
                                        [--expirations 1] [--strikes 10] [--fake]

It records what crosses the broker interface (``s3t.broker.recording``) into
``<data_dir>/recordings/NAME``, which the configuration names and git ignores (GEN-07). The
replay broker serves it back (``s3t.broker.replay``).

Per symbol it reads the instrument, the option chain and the market metrics, and subscribes
Quote, Trade, Summary and Profile of the underlying, and Quote, Greeks and Summary of the
options nearest the money. On the live broker this is a stated, read-only use: it refuses
more than 500 subscriptions. ``--fake`` records the synthetic market instead, to try the
tool without the broker. Ctrl-C ends the recording cleanly.

Until part 2 of WP-0.4 the tool calls the stream directly. With the feed it goes through
the subscription manager, the only caller of subscribe (SUB-02).
"""

import argparse
import asyncio
import logging
import sys

from s3t.broker.fake.broker import FakeBroker
from s3t.broker.interface import Broker, BrokerError, EventBatch, EventType, StreamItem, Sub
from s3t.broker.recording import RecordingBroker
from s3t.broker.tastytrade.broker import from_config
from s3t.broker.tastytrade.chain import near_the_money
from s3t.config import load_config
from s3t.secrets import install_redaction

MAX_SUBSCRIPTIONS = 500
UNDERLYING_EVENTS = (EventType.QUOTE, EventType.TRADE, EventType.SUMMARY, EventType.PROFILE)
OPTION_EVENTS = (EventType.QUOTE, EventType.GREEKS, EventType.SUMMARY)


async def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    symbols = [s for s in args.symbols.split(",") if s]
    inner: Broker = FakeBroker() if args.fake else from_config(config)
    try:
        broker = RecordingBroker(inner, config.paths.recordings_dir / args.name)
    except FileExistsError as e:
        await inner.aclose()
        print(f"FAILED: {e}")
        return 1
    prices: dict[str, float] = {}
    batches = 0

    def sink(item: StreamItem) -> None:
        nonlocal batches
        if not isinstance(item, EventBatch):
            return
        batches += 1
        if item.event is EventType.TRADE:
            for row in item.rows():
                if isinstance(row["price"], int | float):
                    prices.setdefault(row["eventSymbol"], float(row["price"]))

    try:
        stream = broker.stream(sink)
        stream.start()
        chains = {}
        for symbol in symbols:
            await broker.equity(symbol)
            chains[symbol] = await broker.option_chain(symbol)
        await broker.market_metrics(symbols)
        stream.subscribe([Sub(s, e) for s in symbols for e in UNDERLYING_EVENTS])
        for _ in range(150):
            if all(s in prices for s in symbols):
                break
            await asyncio.sleep(0.1)
        options: list[Sub] = []
        for symbol in symbols:
            if symbol not in prices:
                print(f"no trade price for {symbol}: its options are left out")
                continue
            near = near_the_money(
                chains[symbol], prices[symbol], expirations=args.expirations, strikes=args.strikes
            )
            options += [Sub(o, e) for o in near for e in OPTION_EVENTS]
        total = len(options) + len(symbols) * len(UNDERLYING_EVENTS)
        if total > MAX_SUBSCRIPTIONS and not args.fake:
            limit = f"at most {MAX_SUBSCRIPTIONS} on the live broker"
            print(f"FAILED: {total} subscriptions asked, {limit}")
            return 1
        stream.subscribe(options)
        print(f"recording {total} subscriptions for {args.minutes:g} minutes; Ctrl-C ends it")
        await asyncio.sleep(args.minutes * 60)
    except BrokerError as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    finally:
        await broker.aclose()
        print(f"recording {args.name}: {batches} batches seen, {broker.lost} items lost")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a broker session into a local recording.")
    parser.add_argument("name", help="the name of the recording: a directory under recordings/")
    parser.add_argument("--config", help="configuration file (default: S3T_CONFIG or config/)")
    parser.add_argument("--symbols", default="SPY", help="underlyings, comma-separated")
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--expirations", type=int, default=1)
    parser.add_argument("--strikes", type=int, default=10, help="strikes per expiration")
    parser.add_argument("--fake", action="store_true", help="record the synthetic market")
    args = parser.parse_args()
    if not args.name.replace("-", "").replace("_", "").isalnum():
        parser.error("the name may hold letters, digits, '-' and '_' only")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    install_redaction(handler)
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
