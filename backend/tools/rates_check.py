"""Live check of the rate download from FRED (REF-01; WP-0.6).

    uv run python tools/rates_check.py [--config PATH] [--keep]

It downloads the six series once, one request a second, through the code of the application, and
prints per series the size, the number of values, the first and the last day, and the curve that
follows for now. FRED is public and needs no key; a request names the series and nothing else.
With ``--keep`` the files go where the application keeps them (``paths.data_dir``); without it they
go to a temporary directory that is removed. No broker is involved.
"""

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from s3t.config import load_config
from s3t.pricing.rates import SERIES
from s3t.refdata.fred import parse_csv
from s3t.refdata.rates import Rates


async def check(directory: Path, rates: Rates) -> int:
    print(f"load average before: {os.getloadavg()}")
    begun = time.monotonic()
    done = await rates.refresh()
    print(f"{'complete' if done else 'incomplete'} after {time.monotonic() - begun:.1f} s")
    print(f"load average after: {os.getloadavg()}")
    for series in SERIES:
        file = directory / f"{series}.csv"
        if not await asyncio.to_thread(file.exists):
            print(f"  {series:7} no file")
            continue
        text = await asyncio.to_thread(file.read_text)
        values = parse_csv(text, series)
        lines = len(text.splitlines()) - 1
        print(
            f"  {series:7} {len(text)} bytes, {lines} days, {len(values)} values, "
            f"{values[0][0]} to {values[-1][0]}"
        )
    status = rates.status(datetime.now(UTC))
    print(f"in use: {status.source}{f' ({status.reason})' if status.reason else ''}")
    for knot in status.knots:
        print(f"  {knot.days:4} days  {knot.rate:8.4%}  {knot.series} of {knot.observed}")
    if status.last_error:
        print(f"error: {status.last_error}")
    return 0 if done else 1


async def run(directory: Path, rates: Rates) -> int:
    try:
        return await check(directory, rates)
    finally:
        await rates.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the rate series once.")
    parser.add_argument("--config", type=Path, help="the configuration file")
    parser.add_argument("--keep", action="store_true", help="keep the files in the data directory")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    config = load_config(args.config)
    with tempfile.TemporaryDirectory() as temporary:
        directory = config.paths.rates_dir if args.keep else Path(temporary)
        return asyncio.run(run(directory, Rates(directory, config.rates)))


if __name__ == "__main__":
    sys.exit(main())
