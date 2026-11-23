"""The size of a recording's stream file: zstd against gzip (BRK-05; WP-0.4).

    uv run python tools/recording_size.py [--minutes 10] [--strikes 40]

It records the fake broker through the feed into a temporary directory, on a virtual clock
that starts inside a regular session of the synthetic market (Friday 2026-11-20, 10:00 New
York), so the result does not depend on the hour it is run at. Then it compares, on the
same lines:

- zstd as the recorder writes it on a live session: one frame with a block flush every
  3 s of the data, so that a recording that was cut off is readable up to there;
- zstd with a frame ended every 3 s, as first built and given up on 2026-11-24: each frame
  starts without what the compressor has learnt;
- zstd in one frame without a flush, to show what the flushes cost;
- gzip level 5 in one stream, the setting of WP-0.4 part 1.

It prints sizes, not timings. Nothing is kept: the directory is removed at the end. The
data is synthetic, so the ratio on a real session can differ.
"""

import argparse
import asyncio
import gzip
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backports import zstd

from s3t.broker.fake.broker import FakeBroker
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.fake.market import UNDERLYINGS
from s3t.broker.interface import CandlePeriod, EventType, Sub
from s3t.broker.recording import FLUSH_SECONDS, STREAM, RecordingBroker
from s3t.broker.replay import read_lines
from s3t.broker.tastytrade.chain import near_the_money
from s3t.subs.feed import Priority
from s3t.subs.local import LocalFeed

START = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
UNDERLYING_EVENTS = (EventType.QUOTE, EventType.TRADE, EventType.SUMMARY, EventType.PROFILE)
OPTION_EVENTS = (EventType.QUOTE, EventType.GREEKS, EventType.SUMMARY)


async def record(directory: Path, minutes: float, strikes: int) -> int:
    """Record the fake broker for this many minutes of its own clock. Returns the number of
    subscriptions."""
    clock = VirtualClock(START)
    fake = FakeBroker(clock=clock.monotonic, sleep=clock.sleep, now=clock.wall, burst=20)
    broker = RecordingBroker(fake, directory)
    feed = LocalFeed(broker, clock=clock.monotonic, sleep=clock.sleep)
    chain = await broker.option_chain("SPY")
    near = near_the_money(chain, UNDERLYINGS["SPY"].spot, expirations=2, strikes=strikes)
    wanted = [Sub(s, e) for s in ("SPY", "SPX", "AAPL") for e in UNDERLYING_EVENTS]
    wanted += [Sub(o, e) for o in near for e in OPTION_EVENTS]
    wanted.append(Sub("SPY", EventType.CANDLE, CandlePeriod(1, "minute"), START - timedelta(1)))
    feed.consumer("size", Priority.RECORDER).set(wanted)
    feed.start()
    ticker = asyncio.create_task(fake.dxlink.run(clock.sleep))
    for _ in range(round(minutes * 60)):
        await clock.advance(1.0)
    ticker.cancel()
    await feed.aclose()
    await broker.aclose()
    return len(wanted)


def sizes(directory: Path) -> dict[str, int]:
    lines = list(read_lines(directory / STREAM))
    raw = [json.dumps(line, separators=(",", ":")).encode() + b"\n" for line in lines]
    windows: list[bytes] = []  # the lines of each 3 s of the data
    window: list[bytes] = []
    ends = None
    for line, text in zip(lines, raw, strict=True):
        at = line.get("t")
        if isinstance(at, int) and ends is not None and at >= ends and window:
            windows.append(b"".join(window))
            window = []
        if isinstance(at, int) and (ends is None or at >= ends):
            ends = at + round(FLUSH_SECONDS * 1_000_000)
        window.append(text)
    if window:
        windows.append(b"".join(window))
    whole = b"".join(raw)
    one = zstd.ZstdCompressor()
    blocks = sum(len(one.compress(w, one.FLUSH_BLOCK)) for w in windows) + len(one.flush())
    return {
        "lines": len(lines),
        "frames": len(windows),
        "raw": len(whole),
        "zstd_frames": sum(len(zstd.compress(w)) for w in windows),
        "zstd_blocks": blocks,
        "zstd_one": len(zstd.compress(whole)),
        "gzip5": len(gzip.compress(whole, compresslevel=5)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Size of a recording: zstd against gzip.")
    parser.add_argument("--minutes", type=float, default=10.0, help="minutes of the fake's clock")
    parser.add_argument("--strikes", type=int, default=40, help="strikes per expiration")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "size"
        subscriptions = asyncio.run(record(directory, args.minutes, args.strikes))
        s = sizes(directory)
    print(f"{subscriptions} subscriptions, {args.minutes:g} minutes of the synthetic market")
    print(f"lines {s['lines']}, uncompressed {s['raw']} bytes")
    for name, key in (
        (f"zstd, one frame, a block flush every {FLUSH_SECONDS:g} s (as built)", "zstd_blocks"),
        (
            f"zstd, a frame every {FLUSH_SECONDS:g} s ({s['frames']} frames; given up)",
            "zstd_frames",
        ),
        ("zstd, one frame without a flush", "zstd_one"),
        ("gzip level 5, one stream", "gzip5"),
    ):
        print(f"{name}: {s[key]} bytes, {s['raw'] / s[key]:.1f} times smaller than the lines")
    print(f"as built against gzip: {s['zstd_blocks'] / s['gzip5']:.2f} of its size")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
