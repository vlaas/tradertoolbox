"""The state file (STO-01): opening it, the transactions on it, the settings."""

import asyncio
import logging
import math
import os
import sqlite3
import stat
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from s3t.instants import to_micros
from s3t.store.migrate import Migration, StoreError, load_migrations
from s3t.store.settings import all_settings, delete_setting, get_setting, set_setting
from s3t.store.state import StateStore

from .conftest import NOW, STEPS, content, put, schema

pytestmark = pytest.mark.anyio


async def opened(path: Path, migrations: tuple[Migration, ...] | None = None) -> StateStore:
    return await StateStore.open(path, migrations=migrations, now=lambda: NOW)


async def test_the_file_is_made_private_and_in_wal_mode(path: Path) -> None:
    store = await opened(path)
    pragmas = {
        name: await store.read(lambda conn, n=name: conn.execute(f"PRAGMA {n}").fetchone()[0])
        for name in ("journal_mode", "synchronous", "foreign_keys", "trusted_schema")
    }
    assert pragmas == {"journal_mode": "wal", "synchronous": 2, "foreign_keys": 1} | {
        "trusted_schema": 0
    }
    await store.write(lambda conn: set_setting(conn, "a", 1, NOW))
    files = {f.name for f in path.parent.iterdir()}
    assert {"state.sqlite", "state.sqlite-wal", "state.sqlite-shm"} == files
    for name in files:
        assert stat.S_IMODE((path.parent / name).stat().st_mode) == 0o600, name
    status = await store.status()
    assert status.version == len(load_migrations()) and status.size > 0
    assert status.sqlite == sqlite3.sqlite_version
    await store.aclose()
    assert {f.name for f in path.parent.iterdir()} == {"state.sqlite"}  # nothing else is needed


async def test_settings_survive_a_restart(path: Path) -> None:
    store = await opened(path)
    value = {"zone": "America/New_York", "widths": [1, 2.5], "on": True, "none": None}
    await store.write(lambda conn: set_setting(conn, "chart", value, NOW))
    await store.write(lambda conn: set_setting(conn, "other", "x", NOW))
    await store.write(lambda conn: set_setting(conn, "other", "y", NOW))
    await store.aclose()

    store = await opened(path)
    assert await store.read(lambda conn: get_setting(conn, "chart")) == value
    assert await store.read(lambda conn: get_setting(conn, "absent", 7)) == 7
    assert await store.read(all_settings) == {"chart": value, "other": "y"}
    stamp = await store.read(lambda c: c.execute("SELECT updated_at_us FROM setting").fetchone())
    assert stamp == (to_micros(NOW),)
    assert await store.write(lambda conn: delete_setting(conn, "other")) is True
    assert await store.write(lambda conn: delete_setting(conn, "other")) is False
    await store.aclose()


async def test_what_a_setting_refuses(path: Path) -> None:
    store = await opened(path)
    with pytest.raises(ValueError):
        await store.write(lambda conn: set_setting(conn, "a", math.nan, NOW))
    with pytest.raises(ValueError, match="aware"):  # GEN-10
        naive = datetime(2026, 11, 25, 10, 15)
        await store.write(lambda conn: set_setting(conn, "a", 1, naive))
    with pytest.raises(sqlite3.IntegrityError):
        await store.write(lambda conn: set_setting(conn, "", 1, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        await store.write(lambda c: c.execute("INSERT INTO setting VALUES ('a', 'not json', 0)"))
    with pytest.raises(sqlite3.IntegrityError):  # STRICT: no text where an instant belongs
        await store.write(lambda c: c.execute("INSERT INTO setting VALUES ('a', '1', 'today')"))
    assert await store.read(all_settings) == {}
    await store.aclose()


async def test_an_error_rolls_the_transaction_back(path: Path) -> None:
    store = await opened(path)

    def half(conn: sqlite3.Connection) -> None:
        set_setting(conn, "a", 1, NOW)
        raise RuntimeError("half way")

    with pytest.raises(RuntimeError, match="half way"):
        await store.write(half)
    assert await store.read(all_settings) == {}
    await store.write(lambda conn: set_setting(conn, "b", 2, NOW))  # and the store goes on
    assert await store.read(all_settings) == {"b": 2}
    await store.aclose()


async def test_a_read_cannot_write(path: Path) -> None:
    store = await opened(path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        await store.read(lambda conn: set_setting(conn, "a", 1, NOW))
    await store.write(lambda conn: set_setting(conn, "a", 1, NOW))
    assert await store.read(all_settings) == {"a": 1}
    await store.aclose()


async def test_a_function_that_ends_the_transaction(path: Path) -> None:
    store = await opened(path)

    def ends(conn: sqlite3.Connection) -> None:
        conn.execute("COMMIT")

    with pytest.raises(StoreError, match="ended its own transaction"):
        await store.write(ends)
    await store.write(lambda conn: set_setting(conn, "a", 1, NOW))
    await store.aclose()


async def test_transactions_from_many_tasks_come_one_after_another(path: Path) -> None:
    store = await opened(path)
    threads: set[str] = set()

    def increment(conn: sqlite3.Connection) -> None:
        threads.add(threading.current_thread().name)
        set_setting(conn, "n", get_setting(conn, "n", 0) + 1, NOW)

    await asyncio.gather(*(store.write(increment) for _ in range(50)))
    assert await store.read(lambda conn: get_setting(conn, "n")) == 50
    assert len(threads) == 1 and threading.current_thread().name not in threads
    await store.aclose()


async def test_closing_waits_for_a_transaction_under_way(path: Path) -> None:
    store = await opened(path)
    go = threading.Event()

    def slow(conn: sqlite3.Connection) -> None:
        go.wait(5)
        set_setting(conn, "a", 1, NOW)

    writing = asyncio.ensure_future(store.write(slow))
    await asyncio.sleep(0)
    closing_ = asyncio.ensure_future(store.aclose())
    await asyncio.sleep(0.01)
    assert not closing_.done()
    go.set()
    await asyncio.gather(writing, closing_)
    store = await opened(path)
    assert await store.read(all_settings) == {"a": 1}
    await store.aclose()


# ---------------------------------------------------------------- opening


async def test_a_copy_is_made_before_a_migration(path: Path) -> None:
    store = await opened(path, STEPS[:2])
    await store.write(lambda c: c.execute("INSERT INTO person (id, name) VALUES (1, 'Ada')"))
    await store.aclose()
    assert not (path.parent / "backups").exists()  # a new file and an unchanged one need none

    store = await opened(path, STEPS)
    assert (await store.status()).version == 3
    await store.aclose()
    copy = path.parent / "backups" / "2026" / "state-v0002-20261125T101500Z.sqlite"
    assert [f for f in copy.parent.iterdir()] == [copy]
    with closing(sqlite3.connect(copy, autocommit=True)) as conn:
        assert conn.execute("SELECT name, age FROM person").fetchall() == [("Ada", None)]
        assert conn.execute("SELECT max(version) FROM schema_migration").fetchone() == (2,)


async def test_a_failed_migration_leaves_the_file_at_its_version(path: Path) -> None:
    store = await opened(path, STEPS[:1])
    await store.aclose()
    with pytest.raises(StoreError, match="0002_bad"):
        await opened(path, (STEPS[0], Migration(2, "bad", "CREATE TABLE")))
    store = await opened(path, STEPS[:1])
    assert (await store.status()).version == 1
    await store.aclose()


@pytest.mark.parametrize("script", ["CREATE TABLE theirs (x)", "PRAGMA application_id=77"])
async def test_a_file_that_is_not_ours_is_not_touched(path: Path, script: str) -> None:
    path.parent.mkdir()
    with closing(sqlite3.connect(path, autocommit=True)) as conn:
        conn.execute("PRAGMA page_size=4096")
        conn.execute(script)
    before = content(path)
    with pytest.raises(StoreError, match="not a state file"):
        await opened(path)
    assert content(path) == before  # not even its journal mode


async def test_a_newer_file_is_not_touched(path: Path) -> None:
    store = await opened(path, STEPS)
    await store.aclose()
    before = content(path)
    with pytest.raises(StoreError, match="newer application"):
        await opened(path, STEPS[:2])
    assert content(path) == before


async def test_a_file_that_is_no_database(path: Path) -> None:
    put(path, b"this is not a database, and it is longer than a header of 100 bytes" * 3)
    with pytest.raises(StoreError, match="not a readable SQLite file"):
        await opened(path)


async def test_a_file_open_to_others_is_reported(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await (await opened(path)).aclose()
    os.chmod(path, 0o644)
    with caplog.at_level(logging.WARNING, "s3t.store"):
        await (await opened(path)).aclose()
    assert "chmod 600" in caplog.text and str(path.parent) not in caplog.text


async def test_a_second_process_waits_for_the_first(path: Path) -> None:
    """Two connections on one file: a write waits for the other's, up to the busy timeout."""
    store = await opened(path)
    with closing(sqlite3.connect(path, autocommit=True)) as other:
        other.execute("BEGIN IMMEDIATE")
        writing = asyncio.ensure_future(store.write(lambda conn: set_setting(conn, "a", 1, NOW)))
        await asyncio.sleep(0.05)
        assert not writing.done()
        other.execute("COMMIT")
        await writing
    assert await store.read(all_settings) == {"a": 1}
    await store.aclose()


async def test_the_schema_of_the_real_file(path: Path) -> None:
    store = await opened(path)
    assert {name for name, _ in await store.read(schema)} >= {"setting", "schema_migration"}
    await store.aclose()
