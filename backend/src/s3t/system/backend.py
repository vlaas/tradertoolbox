"""The composition of the backend: every part, in the order in which they need each other.

:func:`open_backend` builds and starts them, gives them out and closes them in the opposite order,
also when one of them fails on the way up. Nothing here serves a request; ``s3t.api.server`` does,
on a socket that was opened before (so that a second backend stops before it opens a second broker
session and a second writer of the state file).

The broker is chosen in the configuration (GEN-11) and logged.

- **live**: the Tastytrade broker. The exchange calendar tells its stall rule when data is expected
  (BRK-03).
- **fake**: the synthetic market, with the same calendar and limits. Its clock can start at a
  configured instant, because it moves in the regular session only. The rates are the flat one it
  is priced on; nothing is read or downloaded.
- **replay**: a recording. The calendar is built for the date of the recording. The rates come from
  the files and are not downloaded, so that a run can be repeated.

The state file is the same one whichever broker is chosen; the configuration names it.

A time that belongs to a login or a file is the wall clock's. Everything about the market is the
broker's ``data_time``.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher

from s3t.broker.fake.broker import FakeBroker
from s3t.broker.interface import Broker
from s3t.broker.limits import Clock, Sleep
from s3t.broker.replay import ReplayBroker
from s3t.broker.tastytrade.broker import from_config
from s3t.config import Config
from s3t.market.registry import Instruments
from s3t.market.state import MarketState
from s3t.refdata.calendar import ExchangeCalendar, load_calendar
from s3t.refdata.rates import Rates
from s3t.store.daily import DailyCopies
from s3t.store.state import StateStore
from s3t.subs.local import LocalFeed
from s3t.system.features import Features
from s3t.system.login import Login
from s3t.system.process import ProcessWatch

NY = ZoneInfo("America/New_York")

LOG = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Time:
    """The clocks of the process. A test gives its own."""

    now: Callable[[], datetime] = _utc_now
    clock: Clock = time.monotonic
    sleep: Sleep = asyncio.sleep

    def starting_at(self, start: datetime) -> Callable[[], datetime]:
        """A wall clock that starts at this instant now."""
        offset = start - self.now()
        return lambda: self.now() + offset


@dataclass(frozen=True)
class Backend:
    """The parts of the running backend."""

    config: Config
    time: Time
    started_at: datetime
    broker: Broker
    calendar: ExchangeCalendar
    store: StateStore
    login: Login
    copies: DailyCopies
    rates: Rates
    feed: LocalFeed
    market: MarketState
    instruments: Instruments
    features: Features
    process: ProcessWatch


async def _calendar(config: Config, at: datetime) -> ExchangeCalendar:
    # In a thread: the first build takes a second or so (the plan, section 8).
    day = at.astimezone(NY).date()
    return await asyncio.to_thread(load_calendar, day, config.calendar.closures)


async def make_broker(config: Config, clocks: Time) -> tuple[Broker, ExchangeCalendar]:
    """The configured broker and the calendar that goes with the time of its data."""
    chosen = config.broker
    if chosen.source == "replay":
        assert chosen.recording is not None  # the configuration requires it
        replay = ReplayBroker(config.paths.recordings_dir / chosen.recording, sleep=clocks.sleep)
        return replay, await _calendar(config, replay.data_time())
    if chosen.source == "fake":
        start = chosen.fake_start
        now = clocks.now if start is None else clocks.starting_at(start)
        calendar = await _calendar(config, now())
        fake = FakeBroker(
            calendar=calendar,
            clock=clocks.clock,
            sleep=clocks.sleep,
            now=now,
            stream=config.stream,
            subscriptions=config.subscriptions,
            rate=chosen.rest_rate,
            burst=chosen.rest_burst,
        )
        return fake, calendar
    calendar = await _calendar(config, clocks.now())
    return from_config(config, data_expected=calendar.market_open), calendar


def make_rates(config: Config, broker: Broker, clocks: Time) -> Rates:
    """The rates, which only the live broker downloads."""
    live = broker.source().kind == "live"
    settings = config.rates if live else config.rates.model_copy(update={"download": False})
    return Rates(config.paths.rates_dir, settings, now=clocks.now, sleep=clocks.sleep)


@asynccontextmanager
async def open_backend(
    config: Config,
    *,
    clocks: Time | None = None,
    broker: Broker | None = None,
    hasher: PasswordHasher | None = None,
) -> AsyncGenerator[Backend]:
    """The running backend. ``broker`` stands in for the configured one in a test."""
    clocks = clocks or Time()
    async with AsyncExitStack() as closing:
        if broker is None:
            broker, calendar = await make_broker(config, clocks)
        else:
            calendar = await _calendar(config, broker.data_time())
        closing.push_async_callback(broker.aclose)
        source = broker.source()
        LOG.info("the broker is %s%s", source.kind, f": {source.mark}" if source.mark else "")

        store = await StateStore.open(config.paths.state_file, backup_dir=config.paths.backups)
        closing.push_async_callback(store.aclose)
        lifetime = timedelta(hours=config.login.session_hours)
        login = Login(store, lifetime=lifetime, hasher=hasher, now=clocks.now, clock=clocks.clock)
        if not await login.has_password():
            LOG.warning("no password is set, so nobody can log in: use tools/set_password.py")
        copies = DailyCopies(
            config.paths.state_file,
            config.paths.backups,
            on=config.store.daily_copy,
            now=clocks.now,
            sleep=clocks.sleep,
        )
        closing.push_async_callback(copies.aclose)

        rates = make_rates(config, broker, clocks)
        closing.push_async_callback(rates.aclose)
        if source.kind != "fake":
            await rates.load()

        feed = LocalFeed(broker, config.subscriptions, clock=clocks.clock, sleep=clocks.sleep)
        closing.push_async_callback(feed.aclose)
        # Before the feed starts, so that it sees every item, and closed before it.
        market = MarketState(
            feed,
            now=broker.data_time,
            period=config.stream.aggregation_period,
            sleep=clocks.sleep,
        )
        closing.push_async_callback(market.aclose)
        if isinstance(broker, FakeBroker):
            ticker = asyncio.create_task(broker.dxlink.run(clocks.sleep), name="fake-ticker")
            closing.push_async_callback(_cancel, ticker)

        process = ProcessWatch(config.process.memory_bound_mb, now=clocks.now, sleep=clocks.sleep)
        closing.push_async_callback(process.aclose)

        process.start()
        copies.start()
        rates.start()
        feed.start()
        market.start()
        yield Backend(
            config=config,
            time=clocks,
            started_at=clocks.now(),
            broker=broker,
            calendar=calendar,
            store=store,
            login=login,
            copies=copies,
            rates=rates,
            feed=feed,
            market=market,
            instruments=Instruments(broker),
            features=Features(broker, config.paths.t3_root),
            process=process,
        )


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
