"""The composition: the choice of broker, what each gets, and the order of closing (WP-0.7)."""

import asyncio
import logging
import multiprocessing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from api.conftest import ASOF, make_config, running
from s3t.broker.fake.broker import FakeBroker
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.interface import BrokerError
from s3t.broker.tastytrade.broker import Tastytrade
from s3t.config import Config
from s3t.secrets import SecretsError
from s3t.store.state import StateStore
from s3t.subs.local import LocalFeed
from s3t.system.backend import Time, make_broker, make_rates, open_backend
from s3t.system.health import health

pytestmark = pytest.mark.anyio

BLACK_FRIDAY = datetime(2026, 11, 27, 19, 0, tzinfo=UTC)  # 14:00 in New York, closed since 13:00
CLOSED = "[calendar]\nclosures = [2026-11-20]\n"


def clocks(at: datetime = ASOF) -> Time:
    clock = VirtualClock(at)
    return Time(now=clock.wall, clock=clock.monotonic, sleep=clock.sleep)


def live_config(tmp_path: Path, more: str = "") -> Config:
    (tmp_path / "broker.env").write_text("TASTY_CLIENT_SECRET=abcdef\nTASTY_REFRESH_TOKEN=ghijkl\n")
    (tmp_path / "broker.env").chmod(0o600)
    return make_config(tmp_path, more)


async def test_live_is_the_default_and_gets_the_calendar(tmp_path: Path) -> None:
    """The stall rule (BRK-03) asks the exchange calendar, with the configured closures."""
    config = live_config(tmp_path, "[subscriptions]\nlimit = 900\nreserve = 100\n" + CLOSED)
    broker, calendar = await make_broker(config, clocks())
    assert isinstance(broker, Tastytrade) and not isinstance(broker, FakeBroker)
    assert broker.source().mark is None
    expected: Any = broker._data_expected
    assert expected == calendar.market_open
    assert not expected(BLACK_FRIDAY) and expected(BLACK_FRIDAY - timedelta(hours=2))
    assert not expected(ASOF)  # the configured closure
    stream = broker.stream(lambda _: None)
    assert stream.status().limit == 900
    await broker.aclose()


async def test_live_without_its_credentials(tmp_path: Path) -> None:
    with pytest.raises(SecretsError, match="no environment file"):
        await make_broker(make_config(tmp_path, ""), clocks())


async def test_the_fake_gets_the_calendar_and_the_limits(tmp_path: Path) -> None:
    more = 'source = "fake"\nrest_rate = 7.0\nrest_burst = 9\n[stream]\nstall_after = 33.0\n'
    broker, calendar = await make_broker(make_config(tmp_path, more + CLOSED), clocks())
    assert isinstance(broker, FakeBroker) and broker.market.calendar is calendar
    assert broker.source().mark == "SYNTHETIC DATA"
    assert (broker.rest_status().rate, broker.rest_status().burst) == (7.0, 9)
    assert not calendar.is_trading_day(date(2026, 11, 20))
    assert broker.data_time() == ASOF
    await broker.aclose()


async def test_the_fake_can_start_at_an_instant(tmp_path: Path) -> None:
    more = 'source = "fake"\nfake_start = 2030-03-06T15:00:00Z\n'
    time = clocks()
    broker, calendar = await make_broker(make_config(tmp_path, more), time)
    start = datetime(2030, 3, 6, 15, 0, tzinfo=UTC)
    assert broker.data_time() == start and calendar.last == date(2037, 12, 31)
    assert time.starting_at(start)() == start
    await broker.aclose()


async def test_a_recording_that_is_not_there(tmp_path: Path) -> None:
    config = make_config(tmp_path, 'source = "replay"\nrecording = "none"\n')
    with pytest.raises(BrokerError, match="no recording named none"):
        await make_broker(config, clocks())


async def test_only_the_live_broker_downloads_rates(tmp_path: Path) -> None:
    """`make e2e` must not call FRED, and a replay must be the same tomorrow."""
    config = make_config(tmp_path)
    fake, _ = await make_broker(config, clocks())
    assert make_rates(config, fake, clocks()).status(ASOF).downloads is False
    config = live_config(tmp_path, "")
    live, _ = await make_broker(config, clocks())
    assert make_rates(config, live, clocks()).status(ASOF).downloads is True
    config = live_config(tmp_path, "[rates]\ndownload = false\n")
    assert make_rates(config, live, clocks()).status(ASOF).downloads is False
    for broker in (fake, live):
        await broker.aclose()


async def test_the_fake_is_priced_on_the_flat_rate(tmp_path: Path) -> None:
    """Also where the files of a live backend lie beside it."""
    config = make_config(tmp_path)
    config.paths.rates_dir.mkdir(parents=True)
    (config.paths.rates_dir / "SOFR.csv").write_text("observation_date,SOFR\n2026-11-19,9.99\n")
    async with running(config, VirtualClock(ASOF)) as rig:
        status = rig.backend.rates.status(rig.backend.broker.data_time())
        assert status.source == "flat" and not status.knots


async def test_the_market_state_sees_every_item(tmp_path: Path) -> None:
    """It is there before the feed starts, so it knows the session from its beginning."""
    async with running(make_config(tmp_path), VirtualClock(ASOF)) as rig:
        await rig.up()
        await rig.clock.settle()
        stream = rig.backend.feed.status().stream
        assert rig.backend.market.live_since == stream.connected_at is not None


async def test_the_pool_and_the_surface_service_do_nothing_until_a_view_takes_a_watch(
    tmp_path: Path,
) -> None:
    """WP-1.1: they are in the composition, and a backend that nobody asks for a surface has no
    worker process, no consumer of the feed, no taker of the market state and no task of theirs,
    and reads nothing from the broker for them. The day run of WP-0.9 is such a backend."""
    async with running(make_config(tmp_path), VirtualClock(ASOF)) as rig:
        await rig.up()
        backend = rig.backend
        refresh = backend.config.surface.refresh_s
        await rig.pass_time(2 * refresh + 5)  # two turns of the refresh timer, if it ran
        pool = backend.pool.status()
        assert pool.started_at is None and pool.processes == 0 and backend.pool.pids() == ()
        assert (pool.under_way, pool.done, pool.failed, pool.broken) == (0, 0, 0, 0)
        assert multiprocessing.active_children() == []
        assert backend.surface.status().watched == ()
        assert backend.feed.status().consumers == () and backend.feed.status().granted == 0
        assert backend.market._takers == []
        names = {task.get_name() for task in asyncio.all_tasks()}
        assert not {name for name in names if name.startswith("surface")}
        paths = [request.path for request in rig.fake.rest.requests]
        assert not any("option-chains" in p or "market-metrics" in p for p in paths)
        doc = (await health(backend)).model_dump(mode="json")
        assert doc["surface"] == {"refresh_s": 30.0, "watched": []}
        assert doc["pool"] == {
            "workers": 2,
            "started_at": None,
            "processes": 0,
            "under_way": 0,
            "done": 0,
            "failed": 0,
            "broken": 0,
        }


async def test_everything_is_closed_in_the_opposite_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    closed: list[str] = []

    def watch(kind: type, name: str) -> None:
        original = kind.aclose  # type: ignore[attr-defined]

        async def aclose(self: object) -> None:
            closed.append(name)
            await original(self)

        monkeypatch.setattr(kind, "aclose", aclose)

    from s3t.analysis.surface import SurfaceService
    from s3t.market.state import MarketState
    from s3t.refdata.rates import Rates
    from s3t.store.daily import DailyCopies
    from s3t.system.pool import NumericsPool

    for kind, name in (
        (SurfaceService, "surface"),
        (NumericsPool, "pool"),
        (MarketState, "market"),
        (LocalFeed, "feed"),
        (Rates, "rates"),
        (DailyCopies, "copies"),
        (StateStore, "store"),
        (FakeBroker, "broker"),
    ):
        watch(kind, name)
    with caplog.at_level(logging.INFO, "s3t"):
        async with running(make_config(tmp_path), VirtualClock(ASOF)) as rig:
            await rig.up()
    # The surface service ends before the pool it hands its fits to, and both before the feed.
    assert closed == ["surface", "pool", "market", "feed", "rates", "copies", "store", "broker"]
    assert "the broker is fake: SYNTHETIC DATA" in caplog.text


async def test_what_was_opened_is_closed_when_a_later_part_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []
    original = FakeBroker.aclose

    async def aclose(self: FakeBroker) -> None:
        closed.append("broker")
        await original(self)

    monkeypatch.setattr(FakeBroker, "aclose", aclose)
    config = make_config(tmp_path)
    config.paths.state_file.parent.mkdir(parents=True)
    config.paths.state_file.write_bytes(b"not a database at all, whatever its name says" * 40)
    with pytest.raises(Exception, match="not a readable SQLite file"):
        async with open_backend(config, clocks=clocks()):
            raise AssertionError("it must not open")
    assert closed == ["broker"]


async def test_no_call_leaves_the_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On the fake broker: neither the broker's hosts nor FRED."""

    async def refuse(*_: object, **__: object) -> httpx.Response:
        raise AssertionError("a request to the network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    async with running(make_config(tmp_path), VirtualClock(ASOF)) as rig:
        await rig.up()
        await rig.log_in()
        socket = await rig.socket()
        socket.subscribe(quotes={"symbol": ["SPY"]})
        await rig.pass_time(3)
        assert socket.of(1)[-1].quotes.rows.bid[0] > 0
