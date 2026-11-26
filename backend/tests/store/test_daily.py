"""The daily copy of the state file (STO-07): one a day, every one kept, in a folder per year."""

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from s3t.store.backup import backup, copy_path
from s3t.store.daily import LOOK_EVERY_S, DailyCopies
from s3t.store.settings import get_setting, set_setting
from s3t.store.state import StateStore

from .conftest import NOW

pytestmark = pytest.mark.anyio

HOUR, DAY = timedelta(hours=1), timedelta(days=1)


class Clock:
    def __init__(self, at: datetime = NOW) -> None:
        self.now = at

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def store(path: Path) -> StateStore:
    opened = await StateStore.open(path)
    await opened.write(lambda conn: set_setting(conn, "a", 1, NOW))
    return opened


def copies(path: Path) -> list[str]:
    """Every file below the folder of the copies, by its place there."""
    directory = path.parent / "backups"
    found = directory.rglob("*") if directory.is_dir() else ()
    return sorted(str(f.relative_to(directory)) for f in found if f.is_file())


async def test_one_a_day_and_every_one_is_kept(path: Path, store: StateStore) -> None:
    clock = Clock()
    daily = DailyCopies(path, path.parent / "backups", now=clock)
    for day in range(5):
        for hour in (0, 1, 12, 23):
            clock.now = NOW + day * DAY + hour * HOUR
            await daily.step()
    names = [f"2026/state-daily-202611{day}T101500Z.sqlite" for day in range(25, 30)]
    assert copies(path) == names
    assert daily.status().last_at == NOW + 4 * DAY and daily.status().last_error is None
    with closing(sqlite3.connect(path.parent / "backups" / names[0])) as conn:
        assert get_setting(conn, "a") == 1
    await store.aclose()


async def test_a_folder_per_year(path: Path, store: StateStore) -> None:
    """And the newest copy is found across the turn of the year."""
    clock = Clock(datetime(2026, 12, 31, 12, 0, tzinfo=UTC))
    daily = DailyCopies(path, path.parent / "backups", now=clock)
    await daily.step()
    clock.now += 13 * HOUR  # 2027 in UTC, and not a day later
    await DailyCopies(path, path.parent / "backups", now=clock).step()
    clock.now += 12 * HOUR
    await DailyCopies(path, path.parent / "backups", now=clock).step()
    assert copies(path) == [
        "2026/state-daily-20261231T120000Z.sqlite",
        "2027/state-daily-20270101T130000Z.sqlite",
    ]
    await store.aclose()


def test_the_year_is_the_one_of_the_name() -> None:
    """In UTC, whatever zone the instant is given in."""
    east = timezone(timedelta(hours=5))
    target = copy_path(Path("/x"), Path("/x/state.sqlite"), datetime(2027, 1, 1, 1, tzinfo=east))
    assert target == Path("/x/2026/state-20261231T200000Z.sqlite")


async def test_a_restart_makes_no_second_copy_of_the_day(path: Path, store: StateStore) -> None:
    clock = Clock()
    for n in range(6):
        clock.now = NOW + n * HOUR
        started = DailyCopies(path, path.parent / "backups", now=clock)
        await started.step()
        assert started.status().last_at == NOW
    assert len(copies(path)) == 1
    await store.aclose()


async def test_the_other_copies_lie_beside_them_and_count_for_nothing(
    path: Path, store: StateStore
) -> None:
    """Those made by hand or before a migration, and those of another state file."""
    directory = path.parent / "backups"
    others = [
        copy_path(directory, path, NOW - HOUR),
        copy_path(directory, path, NOW - HOUR, "v0001"),
        copy_path(directory, path.with_name("other.sqlite"), NOW - HOUR, "daily"),
    ]
    for target in others:
        backup(path, target)
    await DailyCopies(path, directory, now=Clock()).step()
    assert len(copies(path)) == 4 and all(target.is_file() for target in others)


async def test_a_copy_from_a_time_to_come(path: Path, store: StateStore) -> None:
    """The clock was wrong once. It neither stops the copies nor makes one at every look."""
    directory = path.parent / "backups"
    backup(path, copy_path(directory, path, NOW + 400 * DAY, "daily"))
    clock = Clock()
    daily = DailyCopies(path, directory, now=clock)
    for hour in range(4):
        clock.now = NOW + hour * HOUR
        await daily.step()
    assert len(copies(path)) == 2
    await store.aclose()


async def test_a_failure_is_reported_and_tried_again(
    path: Path, store: StateStore, caplog: pytest.LogCaptureFixture
) -> None:
    blocked = path.parent / "backups"
    blocked.write_text("a file where the directory belongs")
    daily = DailyCopies(path, blocked, now=Clock())
    await daily.step()
    await daily.step()
    status = daily.status()
    assert status.failures == 2 and status.last_at is None
    assert status.last_error and str(path.parent) not in status.last_error
    assert caplog.text.count("the daily copy failed") == 1  # once, not every ten minutes
    blocked.unlink()
    await daily.step()
    assert daily.status().failures == 0 and daily.status().last_at == NOW
    await store.aclose()


async def test_the_task(path: Path, store: StateStore) -> None:
    slept: list[float] = []
    looked = asyncio.Event()

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        looked.set()
        await asyncio.Event().wait()

    daily = DailyCopies(path, path.parent / "backups", sleep=sleep)
    daily.start()
    await asyncio.wait_for(looked.wait(), 10)
    assert slept == [LOOK_EVERY_S] and len(copies(path)) == 1
    last = daily.status().last_at
    assert last is not None and last.tzinfo is UTC
    await daily.aclose()

    off = DailyCopies(path, path.parent / "none", on=False)
    off.start()
    await asyncio.sleep(0)
    assert off.status().on is False and not (path.parent / "none").exists()
    await off.aclose()
    await store.aclose()
