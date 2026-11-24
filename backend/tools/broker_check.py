"""Live read-only check of the broker client against Tastytrade (BRK-07; WP-0.3, WP-0.5).

    uv run python tools/broker_check.py [--config PATH]

It reads the configuration and the environment file named there, and calls each
allow-listed read about once, at one request per second. The reads are: the token renewal,
the quote token, SPY and SPX, the SPY and SPX chains, one option of SPY and one of each SPX root
but SPXW (whose terms are known), the /ES futures chain, one future, one future option, and
the metrics of SPY and SPX. That is 13 requests.

It prints:

- the granted scopes, with the account masked;
- per read, the key paths found only in the live document or only in the fake's (keys, not
  values);
- what the instrument model reads out of the live document, and the facts its rules rest on:
  the roots, the expiration and settlement types, the exercise style, the instants of expiry
  and the dollars per point of a futures option;
- the symbol formats, live beside fake;
- the SPY metrics whose units matter (fraction or percent).

No price is printed and nothing is written to disk. Logging goes through the redaction filter.
The tool places no order and changes nothing: the client can only send what the allow-list
holds (GEN-01). It reads the documents as the broker sent them, with the client's own
``_read``, because it compares their keys; the parsing is then the client's.
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.instruments import Expiration, OptionChain, OptionContract
from s3t.broker.interface import BrokerError
from s3t.broker.tastytrade import instruments as parse
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


def _fake_option(market: SyntheticMarket) -> str:
    """Any listed SPY option of the synthetic market: its symbol."""
    doc = market.option_chain("SPY")
    assert doc is not None
    return doc["items"][0]["expirations"][0]["strikes"][0]["call"]


def _first_future(market: SyntheticMarket) -> Any:
    doc = market.futures_option_chain("ES")
    assert doc is not None
    return market.future(doc["futures"][0]["symbol"])


def _fake_future_option(market: SyntheticMarket) -> Any:
    doc = market.futures_option_chain("ES")
    assert doc is not None
    strike = doc["option-chains"][0]["expirations"][0]["strikes"][0]
    return market.future_option(strike["call"])


async def call(label: str, fn: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await fn()
    except BrokerError as e:
        print(f"\n[{label}] {type(e).__name__}: {redact(str(e))}")
        return None


NY = ZoneInfo("America/New_York")


def ny(instant: datetime | None) -> str:
    return "none" if instant is None else f"{instant.astimezone(NY):%Y-%m-%d %H:%M} New York"


def describe_chain(chain: OptionChain) -> None:
    """What the model read: roots, types, counts. No prices, no strikes."""
    for root in chain.roots():
        listed = chain.of_root(root)
        first = listed[0]
        kinds = Counter(f"{e.kind}/{e.settlement}" for e in listed)
        contracts = sum(sum(c is not None for c in (*e.calls, *e.puts)) for e in listed)
        print(
            f"  model: root {root}, {len(listed)} expirations, {contracts} contracts, "
            f"{dict(kinds)}, multiplier {first.multiplier:g}, standard {first.standard}, "
            f"cash settled {first.cash_settled}, exercise {first.exercise}, ticks "
            f"{first.ticks.steps if first.ticks else None}"
        )


def describe_contract(option: OptionContract) -> None:
    print(
        f"  model: {option.root} {option.expiration_date} {option.settlement}, "
        f"{option.exercise}, multiplier {option.multiplier:g}; expires-at "
        f"{ny(option.expires_at)}, stops-trading-at {ny(option.stops_trading_at)}"
    )


def middle(e: Expiration) -> OptionContract | None:
    return e.contract(len(e.strikes) // 2, "call") if e.strikes else None


async def run(client: TastytradeRest, market: SyntheticMarket) -> int:
    failures = 0

    async def raw(name: str, label: str, symbol: str) -> Any:
        nonlocal failures
        doc = await call(label, lambda: client._read(name, symbol=symbol))
        failures += doc is None
        return doc

    async def parsed(label: str, fn: Callable[[], Any]) -> Any:
        nonlocal failures

        async def go() -> Any:
            return fn()

        record = await call(f"{label}, parsing", go)
        failures += record is None
        return record

    token = await call("quote token", client.quote_token)
    print(f"granted scopes: {' '.join(client.granted_scopes or ('(not a JWT)',))}")
    if token is not None:
        print(f"quote token: level {token.level}, address host {token.url.split('/')[2]}")

    for symbol in ("SPY", "SPX"):
        live = await raw("equity", f"equity {symbol}", symbol=symbol)
        if live is not None:
            compare(f"equity {symbol}", live, market.equity(symbol))
            security = await parsed(f"equity {symbol}", lambda doc=live: parse.parse_security(doc))
            if security is not None:
                print(f"  model: {security.kind}, active in the document: {live.get('active')}")

    for symbol in ("SPY", "SPX"):
        live = await raw("option_chain", f"option chain {symbol}", symbol=symbol)
        if live is None:
            continue
        compare(f"option chain {symbol}", live, market.option_chain(symbol))
        chain = await parsed(
            f"option chain {symbol}", lambda doc=live, s=symbol: parse.parse_option_chain(doc, s)
        )
        if chain is None:
            continue
        describe_chain(chain)
        for root in chain.roots():
            if root == "SPXW":
                continue
            listed = chain.of_root(root)
            chosen = middle(listed[min(len(listed) - 1, 5)])
            if chosen is None:
                continue
            doc = await raw("equity_option", f"option of {root}", symbol=chosen.symbol)
            if doc is None:
                continue
            if root == "SPY":
                compare("equity option", doc, market.equity_option(_fake_option(market)))
                print(f"  symbols live: {doc.get('symbol')!r} {doc.get('streamer-symbol')!r}")
            option = await parsed(f"option of {root}", lambda d=doc: parse.parse_equity_option(d))
            if option is not None:
                describe_contract(option)

    fchain = await raw("futures_option_chain", "futures chain /ES", symbol="ES")
    if fchain is not None:
        compare("futures chain /ES", fchain, market.futures_option_chain("ES"))
        chain = await parsed(
            "futures chain /ES", lambda: parse.parse_futures_option_chain(fchain, "/ES")
        )
        listed = sum(len(i.get("expirations", [])) for i in fchain.get("option-chains", []))
        factors = Counter(
            str(e.get("strike-factor"))
            for i in fchain.get("option-chains", [])
            for e in i.get("expirations", [])
        )
        print(f"  strike factors in the document: {dict(factors)}")
        styles = [i.get("exercise-style") for i in fchain.get("option-chains", [])]
        print(f"  exercise style per option-chains item: {styles}")
        if chain is not None:
            print(f"  model: {len(chain.expirations)} of {listed} expirations, futures in order")
            describe_chain(chain)
            future_doc = await raw(
                "future", "future", symbol=chain.futures[0].symbol.removeprefix("/")
            )
            if future_doc is not None:
                compare("future", future_doc, _first_future(market))
                future = await parsed("future", lambda: parse.parse_future(future_doc))
                if future is not None:
                    print(
                        f"  model: {future.root}, dollars per point {future.multiplier:g} (future)"
                        f" against {chain.expirations[0].multiplier:g} (its options)"
                    )
                    print(f"  symbols live: {future.symbol!r} {future.stream_symbol!r}")
            chosen = middle(chain.expirations[min(len(chain.expirations) - 1, 10)])
            if chosen is not None:
                doc = await raw("future_option", "future option", symbol=chosen.symbol)
                if doc is not None:
                    compare("future option", doc, _fake_future_option(market))
                    print(f"  option symbols live: {chosen.symbol!r} {chosen.stream_symbol!r}")
                    option = await parsed("future option", lambda: parse.parse_future_option(doc))
                    if option is not None:
                        describe_contract(option)
                        same = option == chosen
                        print(f"  the contract read alone equals the one of the chain: {same}")

    metrics = await call("market metrics", lambda: client.market_metrics(["SPY", "SPX"]))
    failures += metrics is None
    if metrics:
        compare("market metrics SPY", metrics[0], market.market_metrics("SPY"))
        print("  SPY values whose units matter:")
        for field in UNIT_FIELDS:
            print(f"    {field}: {metrics[0].get(field)!r}")
        ivs = metrics[0].get("option-expiration-implied-volatilities") or [{}]
        print(f"    option-expiration-implied-volatilities[0]: {ivs[0]!r}")
    status = client.rest_status()
    print(
        f"\nstatus: token {status.auth}, renews from {status.renews_from:%H:%M:%S} UTC"
        if status.renews_from
        else f"\nstatus: token {status.auth}"
    )
    print(
        f"  limit {status.rate:g}/s burst {status.burst}; {status.requests_last_minute} requests"
        f" in the last minute; 429 answers {status.rate_limited}; reads that failed {failures}"
    )
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
