"""The daily copy of the state file (STO-07; WP-0.7)."""

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from s3t.store.backup import backup, backup_name
from s3t.store.daily import LOOK_EVERY_S, DailyCopies
from s3t.store.settings import get_setting, set_setting
from s3t.store.state import StateStore

from .conftest import NOW

pytestmark = pytest.mark.anyio

HOUR, DAY = timedelta(hours=1), timedelta(days=1)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def store(path: Path) -> StateStore:
    opened = await StateStore.open(path)
    await opened.write(lambda conn: set_setting(conn, "a", 1, NOW))
    return opened


def copies(path: Path) -> list[str]:
    directory = path.parent / "backups"
    return sorted(f.name for f in directory.iterdir()) if directory.is_dir() else []


async def test_one_a_day_and_the_newest_are_kept(path: Path, store: StateStore) -> None:
    clock = Clock()
    daily = DailyCopies(path, path.parent / "backups", 3, now=clock)
    for day in range(5):
        for hour in (0, 1, 12, 23):
            clock.now = NOW + day * DAY + hour * HOUR
            await daily.step()
    names = [f"state-daily-202611{day}T101500Z.sqlite" for day in (27, 28, 29)]
    assert copies(path) == names
    assert daily.status().last_at == NOW + 4 * DAY and daily.status().last_error is None
    with closing(sqlite3.connect(path.parent / "backups" / names[0])) as conn:
        assert get_setting(conn, "a") == 1
    await store.aclose()


async def test_a_restart_does_not_fill_the_places(path: Path, store: StateStore) -> None:
    clock = Clock()
    for n in range(6):
        clock.now = NOW + n * HOUR
        started = DailyCopies(path, path.parent / "backups", 3, now=clock)
        await started.step()
        assert started.status().last_at == NOW
    assert len(copies(path)) == 1
    await store.aclose()


async def test_only_daily_copies_are_deleted(path: Path, store: StateStore) -> None:
    """Not the copies made by hand or before a migration, not those of another state file, and
    nothing that only looks like one."""
    directory = path.parent / "backups"
    others = [
        backup_name(path, NOW - 99 * DAY),
        backup_name(path, NOW - 99 * DAY, "v0001"),
        backup_name(path.with_name("state-daily.sqlite"), NOW - 99 * DAY, "daily"),
        backup_name(path.with_name("other.sqlite"), NOW - 99 * DAY, "daily"),
    ]
    for name in others:
        backup(path, directory / name)
    strays = ["state-daily-20200101T000000Z.sqlite.keep", "state-daily-.sqlite", "notes.txt"]
    for name in strays:
        (directory / name).write_text("mine")
    clock = Clock()
    daily = DailyCopies(path, directory, 1, now=clock)
    for day in range(3):
        clock.now = NOW + day * DAY
        await daily.step()
    assert copies(path) == sorted([*others, *strays, "state-daily-20261127T101500Z.sqlite"])
    await store.aclose()


async def test_a_copy_from_a_time_to_come(path: Path, store: StateStore) -> None:
    """The clock was wrong once. It neither stops the copies nor makes one at every look."""
    directory = path.parent / "backups"
    backup(path, directory / backup_name(path, NOW + 400 * DAY, "daily"))
    clock = Clock()
    daily = DailyCopies(path, directory, 5, now=clock)
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
    daily = DailyCopies(path, blocked, 3, now=Clock())
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

    daily = DailyCopies(path, path.parent / "backups", 2, sleep=sleep)
    daily.start()
    await asyncio.wait_for(looked.wait(), 10)
    assert slept == [LOOK_EVERY_S] and len(copies(path)) == 1
    last = daily.status().last_at
    assert last is not None and last.tzinfo is UTC
    await daily.aclose()

    none = DailyCopies(path, path.parent / "none", 0)
    none.start()
    await asyncio.sleep(0)
    assert none.status().keep == 0 and not (path.parent / "none").exists()
    await none.aclose()
    await store.aclose()
