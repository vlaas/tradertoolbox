"""Request pacing, 429 and retries against the fake broker (BRK-04)."""

import asyncio
import random
from datetime import UTC, datetime

import pytest

from s3t.broker.fake.clock import FakeClock
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.interface import (
    BrokerProtocolError,
    BrokerUnavailable,
    NotFound,
    RateLimited,
)
from s3t.broker.limits import Backoff, TokenBucket, parse_retry_after
from s3t.broker.tastytrade.rest import TastytradeRest

from .conftest import MakeClient

pytestmark = pytest.mark.anyio

EXACT = Backoff(jitter=False)


def reads(fake: FakeTastytrade) -> list[tuple[float, int]]:
    return [(r.at, r.status) for r in fake.requests if r.path != "/oauth/token"]


# ---------------------------------------------------------------------- the limiter alone


async def test_bucket_allows_the_burst_then_the_rate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(2.0, 4, clock=clock.monotonic, sleep=clock.sleep)
    for _ in range(4):
        await bucket.acquire()
    assert clock.now == 1000.0
    for _ in range(4):
        await bucket.acquire()
    assert clock.now == pytest.approx(1002.0)


async def test_bucket_pause_holds_then_resumes_at_the_rate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(2.0, 4, clock=clock.monotonic, sleep=clock.sleep)
    bucket.pause_until(clock.now + 10)
    await bucket.acquire()
    assert clock.now == pytest.approx(1010.0)
    await bucket.acquire()
    assert clock.now == pytest.approx(1010.5)


def test_bucket_refuses_nonsense() -> None:
    with pytest.raises(ValueError):
        TokenBucket(0, 1)
    with pytest.raises(ValueError):
        TokenBucket(1, 0)


def test_backoff_doubles_to_the_cap() -> None:
    assert [EXACT.bound(i) for i in range(7)] == [1, 2, 4, 8, 16, 30, 30]
    rng = random.Random(1)
    for i in range(7):
        assert 0 <= Backoff().delay(i, rng) <= EXACT.bound(i)
    assert EXACT.delay(3, rng) == 8


def test_retry_after_forms() -> None:
    now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    assert parse_retry_after("3", now) == 3.0
    assert parse_retry_after("1.5", now) == 1.5
    assert parse_retry_after("-4", now) == 0.0
    assert parse_retry_after("Fri, 20 Nov 2026 15:00:12 GMT", now) == 12.0
    assert parse_retry_after("soon", now) is None
    assert parse_retry_after("nan", now) is None
    assert parse_retry_after(None, now) is None


# ---------------------------------------------------------------------- the client


async def test_client_stays_under_the_server_limit(
    make_client: MakeClient, fake: FakeTastytrade
) -> None:
    fake.rate_limit = (5, 1.0)  # burst 2 + 2 per second never exceeds 5 in any second
    client = make_client(rate=2.0, burst=2)
    for _ in range(20):
        await client.equity("SPY")
    assert all(r.status == 200 for r in fake.requests)


async def test_client_recovers_from_a_tighter_server_limit(
    make_client: MakeClient, fake: FakeTastytrade
) -> None:
    fake.rate_limit = (3, 1.0)  # tighter than the client's default burst of 4
    client = make_client()
    for _ in range(12):
        await client.equity("SPY")
    statuses = [r.status for r in fake.requests]
    assert 429 in statuses
    assert statuses.count(200) == 13  # the token and 12 reads


async def test_retry_after_is_honoured(
    client: TastytradeRest, fake: FakeTastytrade, clock: FakeClock
) -> None:
    await client.equity("SPY")
    fake.throttle(1, retry_after=7)
    await client.equity("SPY")
    (_, s0), (t429, s1), (t200, s2) = reads(fake)
    assert (s0, s1, s2) == (200, 429, 200)
    assert t200 - t429 == pytest.approx(7.0)


async def test_a_429_holds_every_request(make_client: MakeClient, fake: FakeTastytrade) -> None:
    client = make_client(rate=10.0, burst=10)
    await client.equity("SPY")
    fake.throttle(1, retry_after=5)
    await asyncio.gather(client.equity("SPY"), client.equity("AAPL"), client.equity("SPX"))
    after = reads(fake)[1:]
    t429 = next(t for t, s in after if s == 429)
    later = [t for t, s in after if s == 200]
    assert len(later) == 3
    assert all(t >= t429 + 5 for t in later)


async def test_429_without_retry_after_backs_off_exponentially(
    make_client: MakeClient, fake: FakeTastytrade
) -> None:
    client = make_client(backoff=EXACT)
    await client.equity("SPY")
    fake.throttle(3)
    await client.equity("SPY")
    times = [t for t, _ in reads(fake)[1:]]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert gaps == pytest.approx([1.0, 2.0, 4.0])
    assert [s for _, s in reads(fake)] == [200, 429, 429, 429, 200]


async def test_retries_exhausted_raise_rate_limited(
    make_client: MakeClient, fake: FakeTastytrade
) -> None:
    client = make_client(backoff=EXACT)
    await client.equity("SPY")
    fake.throttle(10)
    with pytest.raises(RateLimited) as info:
        await client.equity("SPY")
    assert info.value.status == 429
    assert len(reads(fake)) == 1 + EXACT.attempts


async def test_a_long_retry_after_fails_at_once(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    await client.equity("SPY")
    fake.throttle(1, retry_after=600)
    with pytest.raises(RateLimited) as info:
        await client.equity("SPY")
    assert info.value.retry_after == 600
    assert len(reads(fake)) == 2


async def test_429_on_the_token_renewal_is_retried(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    fake.throttle(1, retry_after=2)
    await client.equity("SPY")
    assert fake.issued == 1
    assert [(r.path, r.status) for r in fake.requests] == [
        ("/oauth/token", 429),
        ("/oauth/token", 200),
        ("/instruments/equities/SPY", 200),
    ]


async def test_server_errors_are_retried(client: TastytradeRest, fake: FakeTastytrade) -> None:
    await client.equity("SPY")
    fake.fail(2)
    await client.equity("SPY")
    assert [s for _, s in reads(fake)] == [200, 503, 503, 200]


async def test_server_errors_exhausted(client: TastytradeRest, fake: FakeTastytrade) -> None:
    await client.equity("SPY")
    fake.fail(10, status=502)
    with pytest.raises(BrokerUnavailable) as info:
        await client.equity("SPY")
    assert info.value.status == 502


async def test_network_errors_are_retried(client: TastytradeRest, fake: FakeTastytrade) -> None:
    await client.equity("SPY")
    fake.disconnect(2)
    assert (await client.equity("SPY")).symbol == "SPY"


async def test_network_errors_exhausted(client: TastytradeRest, fake: FakeTastytrade) -> None:
    await client.equity("SPY")
    fake.disconnect(10)
    with pytest.raises(BrokerUnavailable, match="ConnectError"):
        await client.equity("SPY")
    assert len(reads(fake)) == 1 + Backoff().attempts


async def test_not_found_is_not_retried(client: TastytradeRest, fake: FakeTastytrade) -> None:
    with pytest.raises(NotFound) as info:
        await client.equity("NOSUCH")
    assert info.value.status == 404
    assert len(reads(fake)) == 1


async def test_an_unexpected_status_is_a_protocol_error(
    client: TastytradeRest, fake: FakeTastytrade
) -> None:
    await client.equity("SPY")
    fake.fail(1, status=418)
    with pytest.raises(BrokerProtocolError) as info:
        await client.equity("SPY")
    assert info.value.status == 418
