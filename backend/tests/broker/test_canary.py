"""Secrets and the account number never leave the backend (GEN-02, GEN-03, SEC-05; plan 3.6).

Canary values are written into a temporary environment file. Every scenario of the fake
broker runs with all loggers at DEBUG (s3t, httpx, httpcore), through a handler with the
redaction filter. Some log lines put a secret in on purpose. The canaries, and every token
the fake issued, must not appear in the log output, in any error, in any returned value,
or in the repr of the client and the secrets.

The stream token is covered by the second test (WP-0.4): a session with a drop and a
refused token, logged at DEBUG. The recording files are checked in test_replay.py. The routes
(WP-0.7) and the MCP results (R4) extend this test when they exist.
"""

import io
import json
import logging
from collections.abc import Awaitable, Iterator
from pathlib import Path

import pytest

from s3t.broker.fake.clock import FakeClock
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.interface import BrokerError
from s3t.broker.tastytrade.endpoints import Endpoint
from s3t.broker.tastytrade.rest import TastytradeRest
from s3t.secrets import install_redaction, load_broker_secrets

from .conftest import ACCOUNT, CLIENT_SECRET, REFRESH_TOKEN, StreamRig

pytestmark = pytest.mark.anyio

LOGGERS = ("", "s3t", "httpx", "httpcore")


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    install_redaction(handler)
    levels = {name: logging.getLogger(name).level for name in LOGGERS}
    root = logging.getLogger()
    root.addHandler(handler)
    for name in LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


async def test_canaries_never_leave(
    tmp_path: Path, market: SyntheticMarket, clock: FakeClock, captured: io.StringIO
) -> None:
    env = tmp_path / "broker.env"
    env.write_text(
        f"# test file\nTASTY_CLIENT_SECRET={CLIENT_SECRET}\nTASTY_REFRESH_TOKEN='{REFRESH_TOKEN}'\n"
    )
    env.chmod(0o600)
    secrets = load_broker_secrets(env)
    fake = FakeTastytrade(
        market,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
        account=ACCOUNT,
        clock=clock.monotonic,
    )
    client = TastytradeRest(
        secrets,
        transport=fake.transport,
        clock=clock.monotonic,
        sleep=clock.sleep,
        now=clock.wall,
    )
    log = logging.getLogger("s3t.test")
    returned: list[object] = []
    errors: list[BaseException] = []

    async def attempt(call: Awaitable[object]) -> None:
        try:
            returned.append(await call)
        except BrokerError as e:
            errors.append(e)
            log.exception("broker call failed")

    quote = await client.quote_token()
    returned.append(quote)
    await attempt(client.equity("SPY"))
    await attempt(client.option_chain("AAPL"))
    await attempt(client.market_metrics(["SPY", "SPX"]))
    fake.expire_tokens()
    await attempt(client.equity_option("SPY   261120C00600000"))
    fake.throttle(2, retry_after=1)
    await attempt(client.future("/ESZ6"))
    fake.fail(2)
    await attempt(client.futures_option_chain("/ES"))
    fake.disconnect(1)
    await attempt(client.equity("SPX"))
    await attempt(client.equity("NOSUCH"))
    order = Endpoint("order", "POST", "/accounts/{symbol}/orders")
    await attempt(client._send(order, order.path(symbol=ACCOUNT)))
    fake.expire_tokens()
    fake.refuse_renewal()
    await attempt(client.equity("SPY"))

    # Lines that carry secrets on purpose; the filter must catch them.
    token = fake.issued_tokens[0]
    log.debug("raw %s and %s", CLIENT_SECRET, REFRESH_TOKEN)
    log.debug("header Authorization: Bearer %s", token)
    log.debug("body %s", json.dumps({"access_token": token, "refresh_token": REFRESH_TOKEN}))
    log.debug("quote token %s", quote.token.get_secret_value())
    log.info("account /accounts/%s/positions and scope accounts:%s:owner", ACCOUNT, ACCOUNT)
    try:
        raise RuntimeError(f"failed with {REFRESH_TOKEN} for {ACCOUNT}")
    except RuntimeError:
        log.exception("an error that quotes a secret")
    log.debug("stack", stack_info=True)

    canaries = [CLIENT_SECRET, REFRESH_TOKEN, ACCOUNT, *fake.issued_tokens, *fake.quote_tokens]
    text = captured.getvalue()
    surfaces = {
        "log": text,
        "errors": " ".join(f"{e} {e!r}" for e in errors),
        "returned": json.dumps(returned, default=repr),
        "repr": f"{client!r} {secrets!r} {quote!r}",
    }
    for where, surface in surfaces.items():
        leaked = [c for c in canaries if c in surface]
        assert not leaked, f"canary in {where}"

    # The run did what it should: the scenarios happened and were logged.
    assert {type(e).__name__ for e in errors} == {"NotFound", "ForbiddenCall", "AuthError"}
    assert fake.issued >= 2 and len(fake.quote_tokens) == 1
    assert "HTTP Request" in text  # httpx logged its requests
    assert "accounts:<acct>:owner" in text and "/accounts/<acct>/positions" in text
    assert "Bearer ***" in text and "RuntimeError" in text
    await client.aclose()


async def test_the_stream_token_never_leaves(rig: StreamRig, captured: io.StringIO) -> None:
    from s3t.broker.interface import EventType, Sub

    session = await rig.open()
    await session.subscribe([Sub("SPY", EventType.QUOTE), Sub("SPY", EventType.TRADE)])
    rig.dxlink.expire_tokens()
    rig.dxlink.drop()
    await rig.clock.advance(5)
    rig.dxlink.sub_cap = 1
    await session.subscribe([Sub("AAPL", EventType.QUOTE), Sub("AAPL", EventType.TRADE)])
    await rig.clock.settle()
    assert session.status().phase == "connected" and len(rig.rest.quote_tokens) == 2
    logging.getLogger("s3t.test").debug("the token is %s", rig.rest.quote_tokens[1])

    canaries = [CLIENT_SECRET, REFRESH_TOKEN, ACCOUNT, *rig.rest.issued_tokens]
    canaries += rig.rest.quote_tokens
    text = captured.getvalue()
    surfaces = {
        "log": text,
        "items": repr(rig.items),
        "status": repr(session.status()),
        "repr": f"{session!r} {rig.broker!r}",
    }
    for where, surface in surfaces.items():
        assert not [c for c in canaries if c in surface], f"canary in {where}"
    # The run did what it should, and the log says so without the token.
    assert "stream -> AUTH channel 0" in text and "the quote token was refused" in text
    assert "the token is ***" in text and "Subscription limit exceeded" in text
    assert [m["token"] for m in rig.sent("AUTH")][-1] == rig.rest.quote_tokens[1]
