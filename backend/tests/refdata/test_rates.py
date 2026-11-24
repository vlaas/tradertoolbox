"""The rate curve in use (REF-01): the selection, the fallback, the files and the refresh. Every
rate is invented. test_never_looks_ahead is ported from `volcalc` tests/test_refdata.py at commit
f53be83 (test_rates_asof_never_looks_ahead)."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from s3t.broker.fake.clock import VirtualClock
from s3t.config import RatesConfig
from s3t.instants import to_micros
from s3t.pricing.rates import SERIES, TENOR_DAYS, RateCurve, to_continuous
from s3t.refdata.fred import Observations
from s3t.refdata.rates import META, REQUEST_PAUSE_S, Rates, knots_as_of

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)  # a Wednesday, 10:00 in New York
TODAY = NOW.date()
AGE = timedelta(days=10)
QUOTED = {"SOFR": 4.0, "DTB3": 3.9, "DGS1MO": 4.1, "DGS3MO": 4.2, "DGS6MO": 4.3, "DGS1": 4.4}
FULL_ROUND = REQUEST_PAUSE_S * (len(SERIES) - 1)


# ---------------------------------------------------------------- the selection


@st.composite
def histories(draw: st.DrawFn) -> tuple[dict[str, Observations], date]:
    base = date(2024, 1, 1)
    history = {}
    for series in SERIES:
        numbers = sorted(draw(st.sets(st.integers(0, 60), max_size=25)))
        # The value says which day it is of.
        history[series] = tuple((base + timedelta(n), n / 100.0) for n in numbers)
    return history, base + timedelta(draw(st.integers(-2, 70)))


@settings(max_examples=300, deadline=None)
@given(histories())
def test_never_looks_ahead(case: tuple[dict[str, Observations], date]) -> None:
    history, day = case
    knots = {k.days: k for k in knots_as_of(history, day, timedelta(days=10_000))}
    for series, days in TENOR_DAYS.items():
        usable = [o for o in history[series] if o[0] + timedelta(days=1) <= day]
        name = series
        if not usable and series == "DGS3MO":
            name = "DTB3"
            usable = [o for o in history[name] if o[0] + timedelta(days=1) <= day]
        if not usable:
            assert days not in knots
            continue
        knot = knots[days]
        assert knot.observed < day  # never a value from the future
        assert (knot.series, knot.observed) == (name, usable[-1][0])  # the latest usable one
        assert knot.rate == float(to_continuous(name, usable[-1][1] / 100))


def one(day: date, value: float = 4.0) -> Observations:
    return ((day, value),)


def test_a_value_counts_from_the_day_after() -> None:
    history = {"SOFR": one(date(2026, 11, 24))}
    assert knots_as_of(history, date(2026, 11, 24), AGE) == ()
    (knot,) = knots_as_of(history, date(2026, 11, 25), AGE)
    assert (knot.series, knot.days, knot.observed) == ("SOFR", 1, date(2026, 11, 24))
    assert knot.rate == float(to_continuous("SOFR", 0.04))


def test_a_series_that_is_too_old_is_left_out_by_itself() -> None:
    old, edge = TODAY - timedelta(days=11), TODAY - timedelta(days=10)
    history = {"SOFR": one(old), "DGS1MO": one(edge), "DGS1": one(TODAY - timedelta(days=1))}
    assert [k.series for k in knots_as_of(history, TODAY, AGE)] == ["DGS1MO", "DGS1"]


def test_the_bill_stands_in_for_the_three_month_yield() -> None:
    fresh, old = TODAY - timedelta(days=1), TODAY - timedelta(days=40)
    bill = {"DTB3": one(fresh, 3.9)}
    for yields in ({}, {"DGS3MO": one(old)}):  # never published, or too old
        (knot,) = knots_as_of(bill | yields, TODAY, AGE)
        assert (knot.series, knot.days) == ("DTB3", 91)
        assert knot.rate == float(to_continuous("DTB3", 0.039))
    (knot,) = knots_as_of(bill | {"DGS3MO": one(fresh)}, TODAY, AGE)
    assert knot.series == "DGS3MO"


# ---------------------------------------------------------------- the files and the refresh


class Fred:
    """FRED with invented rates: weekdays from ``first`` to the day before ``now``."""

    def __init__(self, clock: VirtualClock, first: date = date(2026, 9, 1)) -> None:
        self.clock, self.first = clock, first
        self.asked: list[tuple[float, str]] = []
        self.refuse: set[str] = set()
        self.down = False
        self.change: Callable[[str, str], str] = lambda series, text: text

    def text(self, series: str) -> str:
        lines = [f"observation_date,{series}"]
        day = self.first
        while day < self.clock.wall().date():
            if day.weekday() < 5:
                lines.append(f"{day.isoformat()},{QUOTED[series]}")
            day += timedelta(days=1)
        return "\n".join(lines) + "\n"

    def handle(self, request: httpx.Request) -> httpx.Response:
        series = request.url.params["id"]
        self.asked.append((self.clock.now, series))
        if self.down:
            raise httpx.ConnectTimeout("no answer")
        if series in self.refuse:
            return httpx.Response(503)
        return httpx.Response(200, text=self.change(series, self.text(series)))

    @property
    def names(self) -> list[str]:
        return [series for _, series in self.asked]


async def inline(function: Callable[..., Any], *args: Any) -> Any:
    """Instead of a thread, which the virtual clock would not wait for."""
    return function(*args)


class Rig:
    def __init__(self, directory: Path, at: datetime = NOW) -> None:
        self.dir = directory / "rates"
        self.clock = VirtualClock(at)
        self.fred = Fred(self.clock)
        self.made: list[Rates] = []

    def rates(self, **config: Any) -> Rates:
        made = Rates(
            self.dir,
            RatesConfig(**config),
            transport=httpx.MockTransport(self.fred.handle),
            now=self.clock.wall,
            sleep=self.clock.sleep,
            run=inline,
        )
        self.made.append(made)
        return made

    async def started(self, **config: Any) -> Rates:
        rates = self.rates(**config)
        await rates.load()
        rates.start()
        await self.clock.advance(FULL_ROUND)
        return rates

    def files(self) -> set[str]:
        return {f.name for f in self.dir.iterdir()} if self.dir.exists() else set()


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    made = Rig(tmp_path)
    yield made
    for rates in made.made:
        await rates.aclose()


async def test_a_download_gives_a_curve(rig: Rig) -> None:
    rates = rig.rates()
    await rates.load()
    assert rates.status(NOW).source == "flat"
    assert rates.status(NOW).reason == "no rates have been downloaded"
    assert rates.curve(NOW) == RateCurve.flat(0.04)

    rates.start()
    await rig.clock.advance(FULL_ROUND)
    assert rig.fred.names == list(SERIES)
    gaps = [b - a for (a, _), (b, _) in zip(rig.fred.asked, rig.fred.asked[1:], strict=False)]
    assert all(gap >= REQUEST_PAUSE_S for gap in gaps)  # one at a time, with a pause
    assert rig.files() == {f"{s}.csv" for s in SERIES} | {META}

    curve = rates.curve(NOW)
    assert curve.source == "curve" and curve.days == (1.0, 30.0, 91.0, 182.0, 365.0)
    for series, days in TENOR_DAYS.items():
        want = float(to_continuous(series, QUOTED[series] / 100))
        assert float(curve.rate(days / 365)) == pytest.approx(want, abs=1e-15)
    status = rates.status(NOW)
    assert (status.source, status.reason, status.failures) == ("curve", None, 0)
    assert [k.series for k in status.knots] == list(TENOR_DAYS)  # no DTB3: DGS3MO is there
    assert {k.observed for k in status.knots} == {date(2026, 11, 24)}
    assert status.refreshed_at == NOW and status.last_error is None
    assert status.next_refresh_at == NOW + timedelta(hours=12)


async def test_the_next_download_comes_when_it_is_due(rig: Rig) -> None:
    rates = await rig.started(refresh_hours=24)
    await rig.clock.advance(24 * 3600 - FULL_ROUND - 1)
    assert len(rig.fred.asked) == len(SERIES)
    await rig.clock.advance(1 + FULL_ROUND)
    assert len(rig.fred.asked) == 2 * len(SERIES)
    later = rig.clock.wall()
    assert {k.observed for k in rates.status(later).knots} == {date(2026, 11, 25)}


async def test_a_restart_uses_the_files_and_waits(rig: Rig) -> None:
    first = await rig.started()
    curve = first.curve(NOW)
    await first.aclose()
    await rig.clock.advance(3600)
    again = await rig.started()
    assert again.curve(NOW) == curve and len(rig.fred.asked) == len(SERIES)
    assert again.status(NOW).refreshed_at == NOW
    await rig.clock.advance(11 * 3600)
    assert len(rig.fred.asked) == 2 * len(SERIES)


async def test_failures_are_tried_again_at_growing_waits(rig: Rig) -> None:
    rig.fred.down = True
    rates = await rig.started()
    status = rates.status(NOW)
    assert (status.source, status.failures) == ("flat", 1)
    assert status.last_error is not None and "SOFR: ConnectTimeout" in status.last_error
    assert status.next_refresh_at == NOW + timedelta(minutes=10)
    rounds = len(rig.fred.asked)
    await rig.clock.advance(12 * 3600)
    starts = [at - rig.fred.asked[0][0] for at, s in rig.fred.asked if s == "SOFR"]
    waits = [round((b - a) / 60) for a, b in zip(starts, starts[1:], strict=False)]
    assert waits[:5] == [10, 20, 40, 80, 120] and set(waits[4:]) == {120}
    assert rounds == len(SERIES)

    rig.fred.down = False
    await rig.clock.advance(2 * 3600 + FULL_ROUND)
    status = rates.status(rig.clock.wall())
    assert (status.source, status.failures, status.last_error) == ("curve", 0, None)


async def test_the_waits_stay_bounded_however_long_it_fails(rig: Rig) -> None:
    """A doubling without end overflows after some months of failures."""
    rates = rig.rates()
    rates._failures, rates._attempted_at = 5000, NOW
    assert rates._due() == NOW + timedelta(hours=2)


async def test_only_the_missing_series_are_asked_again(rig: Rig) -> None:
    rig.fred.refuse = {"DGS6MO", "DTB3"}
    rates = await rig.started()
    assert [k.series for k in rates.status(NOW).knots] == ["SOFR", "DGS1MO", "DGS3MO", "DGS1"]
    assert rates.status(NOW).last_error == "DTB3: HTTP 503; DGS6MO: HTTP 503"
    assert rates.status(NOW).refreshed_at is None and META not in rig.files()
    rig.fred.asked.clear()
    rig.fred.refuse = set()
    await rig.clock.advance(600 + FULL_ROUND)
    assert rig.fred.names == ["DTB3", "DGS6MO"]
    assert len(rates.status(NOW).knots) == 5 and rates.status(NOW).refreshed_at is not None
    rig.fred.asked.clear()
    await rig.clock.advance(12 * 3600 + FULL_ROUND)
    assert rig.fred.names == list(SERIES)  # the next one is complete again


@pytest.mark.parametrize(
    "change",
    [
        lambda series, text: "<html>busy</html>",
        lambda series, text: "\n".join(text.splitlines()[:-5]) + "\n",  # cut at the end
        lambda series, text: text.splitlines()[0] + "\n" + "\n".join(text.splitlines()[20:]),
        lambda series, text: text + "2099-01-02,4.0\n",
        lambda series, text: text.replace(str(QUOTED[series]), "999"),
        lambda series, text: text.replace(series, "OTHER"),
    ],
    ids=["no csv", "cut tail", "cut head", "future", "implausible", "other series"],
)
async def test_a_bad_file_never_replaces_a_good_one(
    rig: Rig, change: Callable[[str, str], str]
) -> None:
    rates = await rig.started()
    before = {f.name: f.read_bytes() for f in rig.dir.iterdir()}
    curve = rates.curve(NOW)
    rig.fred.change = change
    assert not await _refreshed(rig, rates)
    assert {f.name: f.read_bytes() for f in rig.dir.iterdir()} == before
    assert rates.curve(NOW) == curve and rates.status(NOW).failures == 1


async def _refreshed(rig: Rig, rates: Rates) -> bool:
    task = asyncio.ensure_future(rates.refresh())
    await rig.clock.advance(FULL_ROUND)
    return await task


async def test_a_file_that_cannot_be_read_is_left_out(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    await (await rig.started()).aclose()
    (rig.dir / "DGS1.csv").write_text("half a fi")
    (rig.dir / "DGS6MO.csv").unlink()
    rates = rig.rates()
    with caplog.at_level(logging.WARNING):
        await rates.load()
    assert [k.series for k in rates.status(NOW).knots] == ["SOFR", "DGS1MO", "DGS3MO"]
    assert "DGS1" in caplog.text and str(rig.dir) not in caplog.text
    assert rates.status(NOW).refreshed_at is None  # so the next download is due now


@pytest.mark.parametrize(
    "meta",
    [
        "",
        "[]",
        "{}",
        '{"refreshed_at_us": "soon"}',
        '{"refreshed_at_us": null}',
        '{"refreshed_at_us": 1e300}',
        json.dumps({"refreshed_at_us": to_micros(NOW + timedelta(days=900))}),  # to come
    ],
)
async def test_an_unusable_time_of_the_last_download_means_now(rig: Rig, meta: str) -> None:
    await (await rig.started()).aclose()
    (rig.dir / META).write_text(meta)
    rates = rig.rates()
    await rates.load()
    assert rates.status(NOW).refreshed_at is None and rates.status(NOW).source == "curve"


async def test_old_files_give_way_to_the_flat_rate_and_say_so(rig: Rig) -> None:
    rates = await rig.started(flat_rate=0.035)
    last = datetime(2026, 12, 4, 15, 0, tzinfo=UTC)  # ten days after the last value
    assert rates.status(last).source == "curve"
    status = rates.status(last + timedelta(days=1))
    assert (status.source, status.knots) == ("flat", ())
    assert status.reason == "the downloaded rates end on 2026-11-24"
    assert rates.curve(last + timedelta(days=1)) == RateCurve.flat(0.035)


async def test_a_replay_gets_the_rates_of_its_day(rig: Rig) -> None:
    rig.fred.change = lambda series, text: text.replace(
        f"2026-10-14,{QUOTED[series]}", "2026-10-14,1.5"
    )
    rates = await rig.started()
    then = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    assert {k.observed for k in rates.status(then).knots} == {date(2026, 10, 14)}
    assert float(rates.curve(then).rate(1 / 365)) == float(to_continuous("SOFR", 0.015))
    # The day is New York's: late in its evening it is the next day in UTC.
    evening = datetime(2026, 10, 15, 2, 0, tzinfo=UTC)
    assert {k.observed for k in rates.status(evening).knots} == {date(2026, 10, 13)}
    before = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    assert rates.status(before).reason == "the downloaded rates begin after 2026-08-03"
    with pytest.raises(ValueError, match="aware"):
        rates.curve(datetime(2026, 10, 15, 14, 0))


async def test_the_curve_is_kept_for_its_day_and_renewed_by_a_download(rig: Rig) -> None:
    rates = await rig.started()
    assert rates.curve(NOW) is rates.curve(NOW + timedelta(hours=3))
    old = rates.curve(NOW)
    rig.fred.change = lambda series, text: text.replace(str(QUOTED[series]), "2.0")
    assert await _refreshed(rig, rates)
    assert rates.curve(NOW) != old  # revised values are taken


async def test_downloading_can_be_switched_off(rig: Rig) -> None:
    rates = await rig.started(download=False)
    await rig.clock.advance(48 * 3600)
    assert rig.fred.asked == []
    status = rates.status(NOW)
    assert (status.downloads, status.source, status.next_refresh_at) == (False, "flat", None)
    assert rates.curve(NOW) == RateCurve.flat(0.04)


async def test_a_disk_that_cannot_be_written_is_a_failure(rig: Rig) -> None:
    rig.dir.parent.mkdir(exist_ok=True)
    rig.dir.write_text("a file where the directory belongs")
    rates = await rig.started()
    status = rates.status(NOW)
    assert status.failures == 1 and status.last_error is not None
    assert str(rig.dir) not in status.last_error
    assert rates.curve(NOW) == RateCurve.flat(0.04)
    os.unlink(rig.dir)
    await rig.clock.advance(600 + FULL_ROUND)
    assert rates.status(NOW).source == "curve"


async def test_the_task_survives_an_unexpected_error(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    def breaks(series: str, text: str) -> str:
        raise RuntimeError("a fault in the code")

    rig.fred.change = breaks
    with caplog.at_level(logging.ERROR):
        rates = await rig.started()
    assert "a fault in the code" in caplog.text and rates.status(NOW).failures == 1
    rig.fred.change = lambda series, text: text
    await rig.clock.advance(600 + FULL_ROUND)
    assert rates.status(NOW).source == "curve"


async def test_closing_in_the_middle_of_a_download(rig: Rig) -> None:
    rates = rig.rates()
    await rates.load()
    rates.start()
    await rig.clock.advance(1.5)
    await rates.aclose()
    assert 0 < len(rig.fred.asked) < len(SERIES) and rig.clock.sleepers == 0
    assert not [f for f in rig.files() if f.endswith(".partial")]
