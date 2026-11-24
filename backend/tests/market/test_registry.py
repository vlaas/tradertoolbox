"""The instruments read from the broker (MKT-01): once a day, one call at a time."""

import asyncio
from typing import Any

import pytest

from broker.conftest import MakeClient, client, make_client, secrets  # noqa: F401
from s3t.broker.fake.clock import FakeClock
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.interface import BrokerUnavailable, NotFound
from s3t.broker.limits import Backoff
from s3t.market.registry import Instruments

pytestmark = pytest.mark.anyio


def reads(fake: FakeTastytrade, part: str) -> int:  # noqa: F811
    return sum(1 for r in fake.requests if part in r.path and r.status == 200)


async def test_each_document_is_read_once(client: Any, fake: FakeTastytrade) -> None:  # noqa: F811
    registry = Instruments(client)
    spy = await registry.security("SPY")
    assert (spy.symbol, spy.kind) == ("SPY", "etf") and await registry.security("SPY") is spy
    chain = await registry.chain("SPX")
    assert await registry.chain("SPX") is chain
    assert (await registry.future("/ESZ6")).root == "/ES"
    assert (await registry.futures_chain("/ES")).futures[0].symbol == "/ESZ6"
    assert reads(fake, "/equities/") == reads(fake, "/option-chains/SPX") == 1
    assert repr(registry) == "Instruments(4 held)"


async def test_requests_at_the_same_time_make_one_call(client: Any, fake: FakeTastytrade) -> None:  # noqa: F811
    registry = Instruments(client)
    chains = await asyncio.gather(*(registry.chain("AAPL") for _ in range(5)))
    assert all(c is chains[0] for c in chains) and reads(fake, "/option-chains/") == 1


async def test_a_caller_that_gives_up_does_not_end_the_call(
    client: Any,  # noqa: F811
    fake: FakeTastytrade,  # noqa: F811
) -> None:
    registry = Instruments(client)
    first = asyncio.create_task(registry.chain("AAPL"))
    second = asyncio.create_task(registry.chain("AAPL"))
    await asyncio.sleep(0)
    first.cancel()
    assert (await second).underlying == "AAPL" and first.cancelled()
    assert reads(fake, "/option-chains/") == 1


async def test_an_error_is_not_kept(make_client: MakeClient, fake: FakeTastytrade) -> None:  # noqa: F811
    registry = Instruments(make_client(backoff=Backoff(attempts=1)))
    with pytest.raises(NotFound):
        await registry.security("NOSUCH")
    fake.fail(1)
    with pytest.raises(BrokerUnavailable):
        await registry.security("SPY")
    assert (await registry.security("SPY")).symbol == "SPY"


async def test_what_was_read_is_forgotten_on_the_next_day(
    client: Any,  # noqa: F811
    fake: FakeTastytrade,  # noqa: F811
    clock: FakeClock,  # noqa: F811
) -> None:
    """The day of New York: at 10:00 the next one is 14 hours away, in UTC nine."""
    registry = Instruments(client)
    first = await registry.chain("SPY")
    clock.advance(13 * 3600)
    assert await registry.chain("SPY") is first
    clock.advance(2 * 3600)
    assert await registry.chain("SPY") is not first
    assert reads(fake, "/option-chains/SPY") == 2


async def test_the_exercise_style_of_a_root_is_read_once(
    client: Any,  # noqa: F811
    fake: FakeTastytrade,  # noqa: F811
) -> None:
    registry = Instruments(client)
    spx, spy = await registry.chain("SPX"), await registry.chain("SPY")
    assert [await registry.exercise(spx, root) for root in ("SPX", "SPXW")] == ["european"] * 2
    assert await registry.exercise(spy, "SPY") == "american"
    assert await registry.exercise(spy, "SPY") == "american"
    assert reads(fake, "/equity-options/") == 3
    assert await registry.exercise(spy, "NOSUCH") is None
    # A futures chain states it, so nothing is read.
    es = await registry.futures_chain("/ES")
    assert await registry.exercise(es, "EW4") == "american"
    assert reads(fake, "/equity-options/") == 3 and reads(fake, "/future-options/") == 0
