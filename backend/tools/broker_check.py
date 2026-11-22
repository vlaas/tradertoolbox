"""Live read-only check of the broker client against Tastytrade (BRK-07; WP-0.3).

    uv run python tools/broker_check.py [--config PATH]

It reads the configuration and the environment file named there, and calls each
allow-listed read about once, at one request per second. The reads are: the token renewal,
the quote token, SPY and SPX, the SPY chain, one SPY option, the /ES futures chain, one
future, one future option, and the metrics of SPY and SPX. That is about ten requests.

It prints:

- the granted scopes, with the account masked;
- per read, the key paths found only in the live document or only in the fake's (keys, not
  values);
- the symbol formats, live beside fake;
- the SPY metrics whose units matter (fraction or percent).

Nothing is written to disk. Logging goes through the redaction filter. The tool places no
order and changes nothing: the client can only send what the allow-list holds (GEN-01).
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import BrokerError, JsonObject
from s3t.broker.tastytrade.rest import TastytradeRest
from s3t.config import load_config
from s3t.secrets import install_redaction, load_broker_secrets, redact

UNIT_FIELDS = (
    "implied-volatility-index",
    "implied-volatility-index-rank",
    "tos-implied-volatility-index-rank",
    "tw-implied-volatility-index-rank",
    "implied-volatility-percentile",
    "implied-volatility-index-5-day-change",
    "implied-volatility-30-day",
    "historical-volatility-30-day",
    "iv-hv-30-day-difference",
    "dividend-rate-per-share",
    "dividend-yield",
    "beta",
)


def key_paths(doc: Any, prefix: str = "") -> set[str]:
    """Every key path of a JSON value; list elements are merged under ``[]``."""
    paths: set[str] = set()
    if isinstance(doc, dict):
        for key, value in doc.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths |= key_paths(value, path)
    elif isinstance(doc, list):
        for value in doc:
            paths |= key_paths(value, f"{prefix}[]")
    return paths


def compare(name: str, live: Any, fake: Any) -> None:
    live_paths, fake_paths = key_paths(live), key_paths(fake)
    only_live, only_fake = sorted(live_paths - fake_paths), sorted(fake_paths - live_paths)
    print(f"\n[{name}] keys: {len(live_paths)} live, {len(fake_paths)} fake")
    for path in only_live:
        print(f"  only live: {path}")
    for path in only_fake:
        print(f"  only fake: {path}")


def first_strike(chain: JsonObject, item_key: str = "items") -> tuple[JsonObject, JsonObject]:
    """An expiration about a month out and its middle strike."""
    item = chain[item_key][0]
    expirations = item["expirations"]
    exp = min(expirations, key=lambda e: abs(int(e.get("days-to-expiration", 0)) - 30))
    strikes = exp["strikes"]
    return exp, strikes[len(strikes) // 2]


async def call(label: str, fn: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await fn()
    except BrokerError as e:
        print(f"\n[{label}] {type(e).__name__}: {redact(str(e))}")
        return None


async def run(client: TastytradeRest, market: SyntheticMarket) -> int:
    failures = 0
    token = await call("quote token", client.quote_token)
    print(f"granted scopes: {' '.join(client.granted_scopes or ('(not a JWT)',))}")
    if token is not None:
        print(f"quote token: level {token.level}, address host {token.url.split('/')[2]}")

    for symbol in ("SPY", "SPX"):
        live = await call(f"equity {symbol}", lambda s=symbol: client.equity(s))
        failures += live is None
        if live is not None:
            compare(f"equity {symbol}", live, market.equity(symbol))

    chain = await call("option chain SPY", lambda: client.option_chain("SPY"))
    failures += chain is None
    if chain is not None:
        compare("option chain SPY", chain, market.option_chain("SPY"))
        exp, strike = first_strike(chain)
        fake_chain = market.option_chain("SPY")
        assert fake_chain is not None
        _, fake_strike = first_strike(fake_chain)
        print(f"  symbols live: {strike['call']!r} {strike['call-streamer-symbol']!r}")
        print(f"  symbols fake: {fake_strike['call']!r} {fake_strike['call-streamer-symbol']!r}")
        print(
            f"  settlement and type live: {exp.get('settlement-type')} {exp.get('expiration-type')}"
        )
        option = await call("equity option", lambda: client.equity_option(strike["call"]))
        failures += option is None
        if option is not None:
            compare("equity option", option, market.equity_option(fake_strike["call"]))
            print(f"  expires-at live: {option.get('expires-at')}")

    fchain = await call("futures chain /ES", lambda: client.futures_option_chain("/ES"))
    if fchain is None:
        fchain = await call(
            "futures chain /ES, encoded slash",
            lambda: client._read("futures_option_chain", symbol="/ES"),  # pyright: ignore[reportPrivateUsage]
        )
    failures += fchain is None
    if fchain is not None:
        compare("futures chain /ES", fchain, market.futures_option_chain("ES"))
        future_symbol = fchain["futures"][0]["symbol"]
        future = await call("future", lambda: client.future(future_symbol))
        if future is None:
            future = await call(
                "future, encoded slash",
                lambda: client._read("future", symbol=future_symbol),  # pyright: ignore[reportPrivateUsage]
            )
        failures += future is None
        fake_future = market.future(market.futures_option_chain("ES")["futures"][0]["symbol"])  # type: ignore[index]
        if future is not None:
            compare("future", future, fake_future)
            print(f"  symbols live: {future.get('symbol')!r} {future.get('streamer-symbol')!r}")
        exp, strike = first_strike(fchain, "option-chains")
        fake_fchain = market.futures_option_chain("ES")
        assert fake_fchain is not None
        _, fake_strike = first_strike(fake_fchain, "option-chains")
        print(f"  option symbols live: {strike['call']!r} {strike['call-streamer-symbol']!r}")
        fake_call, fake_streamer = fake_strike["call"], fake_strike["call-streamer-symbol"]
        print(f"  option symbols fake: {fake_call!r} {fake_streamer!r}")
        print(
            f"  settlement and type live: {exp.get('settlement-type')} {exp.get('expiration-type')}"
        )
        option = await call("future option", lambda: client.future_option(strike["call"]))
        failures += option is None
        if option is not None:
            compare("future option", option, market.future_option(fake_strike["call"]))

    metrics = await call("market metrics", lambda: client.market_metrics(["SPY", "SPX"]))
    failures += metrics is None
    if metrics:
        compare("market metrics SPY", metrics[0], market.market_metrics("SPY"))
        print("  SPY values whose units matter:")
        for field in UNIT_FIELDS:
            print(f"    {field}: {metrics[0].get(field)!r}")
        ivs = metrics[0].get("option-expiration-implied-volatilities") or [{}]
        print(f"    option-expiration-implied-volatilities[0]: {ivs[0]!r}")
    print(f"\nrenewals: {client.renewals}; reads that failed: {failures}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--config", help="the configuration file (default: the usual search)")
    args = parser.parse_args(argv)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    install_redaction(handler)
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config(args.config)
    secrets = load_broker_secrets(config.paths.env_file)
    market = SyntheticMarket(datetime.now(UTC))

    async def go() -> int:
        client = TastytradeRest(secrets, rate=1.0, burst=1)
        try:
            return await run(client, market)
        finally:
            await client.aclose()

    return 1 if asyncio.run(go()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
