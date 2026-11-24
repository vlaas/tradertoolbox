"""The rate curve in use: downloaded, kept, and selected for the time of the data (REF-01).

Source of the selection: `volcalc` src/vrp/refdata/rates.py at commit f53be83, `rates_asof` and
`PUBLISH_LAG`.

Changed in the port (2026-11-25, WP-0.6):
- Plain values instead of a polars table.
- A series whose latest value is too old is left out; the source used it forever.
- DTB3 stands in for DGS3MO whenever that has no usable value; the source did so only when it
  had never been published.

**The curve belongs to the time of the data.** :meth:`Rates.curve` takes that instant, so a replay
gets the rates of its day. A value of day d counts from d+1, New York, as in `volcalc`. That is the
rule for a decision on a past day, and it can be some hours early: Treasury yields appear on FRED
in the afternoon of the next business day (from memory, not measured). Live it cannot matter,
because a value that is not out yet is not in the file.

**The flat rate** takes over when no series has a usable value: nothing was downloaded yet, the
files have not been renewed for longer than the maximum age, or a replay is older than the history.
The status says which is in use and why.

**The refresh.** A complete download is repeated after the configured hours. A failed one is tried
again after 10 minutes, then after twice as long each time, at most 2 hours, and only the series
that are missing. Each series is one file in the data directory, exactly as FRED sent it. A file is
replaced only by one that holds at least as much. FRED does not always answer from the owner's
network (note in `backtester`), so the files are what the curve is built from; the network only
renews them.
"""

import asyncio
import json
import logging
import os
from bisect import bisect_right
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from s3t.broker.limits import Backoff, Sleep
from s3t.config import RatesConfig
from s3t.instants import from_micros, to_micros
from s3t.pricing.rates import SERIES, TENOR_DAYS, RateCurve, to_continuous
from s3t.refdata.fred import (
    FredClient,
    Observations,
    RateDataError,
    check_replaces,
    parse_csv,
)

PUBLISH_LAG = timedelta(days=1)
STAND_IN = {"DGS3MO": "DTB3"}
REQUEST_PAUSE_S = 1.0
LONGEST_SLEEP_S = 600.0
RETRY = Backoff(first=600.0, factor=2.0, cap=7200.0, jitter=False)
RETRY_DOUBLINGS = 10  # more than the cap needs; the power must not grow for ever
META = "refreshed.json"

_NY = ZoneInfo("America/New_York")

LOG = logging.getLogger(__name__)

type Run = Callable[..., Awaitable[Any]]
"""Runs a blocking function elsewhere: ``asyncio.to_thread``."""


@dataclass(frozen=True, slots=True)
class Knot:
    """One point of the curve and where it comes from."""

    series: str
    days: int
    observed: date
    """The day the value was observed."""
    rate: float
    """Continuously compounded, per 365-day year."""


def knots_as_of(
    history: Mapping[str, Observations], day: date, max_age: timedelta
) -> tuple[Knot, ...]:
    """The points of the curve on a day: per tenor the latest value published before it, not
    older than ``max_age``. Never a value of the day itself or later."""
    knots = []
    for series, days in TENOR_DAYS.items():
        for name in (series, STAND_IN.get(series)):
            found = None if name is None else _latest(history.get(name, ()), day - PUBLISH_LAG)
            if name is not None and found is not None and day - found[0] <= max_age:
                rate = float(to_continuous(name, found[1] / 100.0))
                knots.append(Knot(name, days, found[0], rate))
                break
    return tuple(knots)


def _latest(observations: Observations, day: date) -> tuple[date, float] | None:
    i = bisect_right(observations, day, key=lambda o: o[0])
    return observations[i - 1] if i else None


@dataclass(frozen=True)
class RatesStatus:
    """What is in use, for the app and the health route (REF-01, SEC-08)."""

    source: Literal["curve", "flat"]
    reason: str | None
    """Why the flat rate is in use."""
    flat_rate: float
    """The configured one."""
    knots: tuple[Knot, ...]
    downloads: bool
    """Whether downloading is switched on."""
    refreshed_at: datetime | None
    """UTC. The last complete download."""
    failures: int
    """Failed attempts since then."""
    last_error: str | None
    """Of the last attempt, if it failed."""
    next_refresh_at: datetime | None
    """UTC. None when downloading is off or not started."""


class Rates:
    """Call :meth:`load` first, then :meth:`start` to keep the files renewed."""

    def __init__(
        self,
        directory: Path,
        config: RatesConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        run: Run = asyncio.to_thread,
    ) -> None:
        self._dir, self._config = directory, config
        self._max_age = timedelta(days=config.max_age_days)
        self._fred = FredClient(transport=transport)
        self._now, self._sleep, self._in_thread = now, sleep, run
        self._history: dict[str, Observations] = {}
        self._refreshed_at: datetime | None = None
        self._attempted_at: datetime | None = None
        self._arrived: set[str] = set()  # of the download under way, over its attempts
        self._failures = 0
        self._error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._cached: tuple[date, RateCurve] | None = None

    def __repr__(self) -> str:
        return f"Rates({len(self._history)} series)"

    # ------------------------------------------------------------------ the curve

    def curve(self, at: datetime) -> RateCurve:
        """The curve for the time of the data."""
        day = _new_york(at)
        if self._cached is None or self._cached[0] != day:
            self._cached = (day, self._build(self._knots(day)))
        return self._cached[1]

    def status(self, at: datetime) -> RatesStatus:
        day = _new_york(at)
        knots = self._knots(day)
        return RatesStatus(
            source="curve" if knots else "flat",
            reason=None if knots else self._why_flat(day),
            flat_rate=self._config.flat_rate,
            knots=knots,
            downloads=self._config.download,
            refreshed_at=self._refreshed_at,
            failures=self._failures,
            last_error=self._error,
            next_refresh_at=self._due() if self._task is not None else None,
        )

    def _knots(self, day: date) -> tuple[Knot, ...]:
        return knots_as_of(self._history, day, self._max_age)

    def _build(self, knots: tuple[Knot, ...]) -> RateCurve:
        if not knots:
            return RateCurve.flat(self._config.flat_rate)
        tenor = {days: series for series, days in TENOR_DAYS.items()}
        return RateCurve.from_rates({tenor[knot.days]: knot.rate for knot in knots})

    def _why_flat(self, day: date) -> str:
        if not self._history:
            return "no rates have been downloaded"
        latest = [f for o in self._history.values() if (f := _latest(o, day - PUBLISH_LAG))]
        if not latest:
            return f"the downloaded rates begin after {day}"
        return f"the downloaded rates end on {max(d for d, _ in latest)}"

    # ------------------------------------------------------------------ the files

    async def load(self) -> None:
        """Read what was downloaded before. A file that cannot be read is logged and left out."""
        self._history, refreshed_at = await self._in_thread(_read_files, self._dir)
        # A time to come would put the next download off until then.
        known = refreshed_at is not None and refreshed_at <= self._now()
        self._refreshed_at = refreshed_at if known else None
        self._cached = None

    def start(self) -> None:
        """Keep the files renewed, in a task of its own. Does nothing when downloading is off."""
        if self._config.download and self._task is None:
            self._task = asyncio.create_task(self._keep_renewed(), name="rates")

    async def refresh(self) -> bool:
        """One attempt to download the series that are missing. Whether all are here now. No
        error of the network or the disk comes out of it: the status has it."""
        self._attempted_at = self._now()
        problems = []
        for number, series in enumerate(s for s in SERIES if s not in self._arrived):
            if number:
                await self._sleep(REQUEST_PAUSE_S)
            try:
                await self._renew(series)
            except (RateDataError, OSError) as e:
                # The text of an OSError holds the path, which belongs to the machine.
                problems.append(f"{series}: {e.strerror if isinstance(e, OSError) else e}")
        if problems:
            self._failures += 1
            self._error = "; ".join(problems)
            LOG.warning("rates: the download failed for %s", self._error)
            return False
        self._arrived.clear()
        self._failures, self._error = 0, None
        self._refreshed_at = self._attempted_at
        try:
            await self._in_thread(_write_meta, self._dir, self._refreshed_at)
        except OSError as e:  # the next start downloads again, and nothing else
            LOG.warning("rates: the time of the download was not kept (%s)", e.strerror)
        LOG.info("rates: %d series downloaded", len(SERIES))
        return True

    async def _renew(self, series: str) -> None:
        text = await self._fred.fetch(series)
        observations = parse_csv(text, series)
        check_replaces(observations, self._history.get(series, ()), _new_york(self._now()))
        await self._in_thread(_write, self._dir / f"{series}.csv", text)
        self._history[series] = observations
        self._arrived.add(series)
        self._cached = None

    async def _keep_renewed(self) -> None:
        while True:
            due = self._due()
            wait = 0.0 if due is None else (due - self._now()).total_seconds()
            if wait > 0:
                # In short sleeps: a machine that was suspended finds out soon after.
                await self._sleep(min(wait, LONGEST_SLEEP_S))
                continue
            try:
                await self.refresh()
            except Exception:
                self._failures += 1
                self._error = "an unexpected error; the log has it"
                LOG.exception("rates: the download failed")

    def _due(self) -> datetime | None:
        """When the next attempt is due; None for now."""
        if self._failures and self._attempted_at is not None:
            wait = RETRY.bound(min(self._failures, RETRY_DOUBLINGS) - 1)
            return self._attempted_at + timedelta(seconds=wait)
        if self._refreshed_at is None:
            return None
        return self._refreshed_at + timedelta(hours=self._config.refresh_hours)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._fred.aclose()


def _new_york(at: datetime) -> date:
    if at.tzinfo is None:
        raise ValueError("an instant is aware (GEN-10)")
    return at.astimezone(_NY).date()


# ---------------------------------------------------------------------- blocking file access


def _read_files(directory: Path) -> tuple[dict[str, Observations], datetime | None]:
    history: dict[str, Observations] = {}
    for series in SERIES:
        try:
            text = (directory / f"{series}.csv").read_text(encoding="utf-8")
            history[series] = parse_csv(text, series)
        except FileNotFoundError:
            continue
        except (RateDataError, OSError, UnicodeDecodeError) as e:
            LOG.warning("rates: the file of %s is left out (%s)", series, type(e).__name__)
    refreshed_at = None
    if len(history) == len(SERIES):  # else the next download is due now
        try:
            meta = json.loads((directory / META).read_text(encoding="utf-8"))
            refreshed_at = from_micros(int(meta["refreshed_at_us"]))
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            pass
    return history, refreshed_at


def _write(path: Path, text: str) -> None:
    """Whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def _write_meta(directory: Path, refreshed_at: datetime) -> None:
    _write(directory / META, json.dumps({"refreshed_at_us": to_micros(refreshed_at)}))
