"""The copy of the state file (STO-07): consistent, whole, and possible while the backend runs."""

import asyncio
import sqlite3
import stat
import subprocess
import sys
import textwrap
from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from s3t.store.backup import backup, backup_name
from s3t.store.migrate import StoreError, load_migrations
from s3t.store.settings import get_setting, set_setting
from s3t.store.state import StateStore

from .conftest import NOW, content, put

pytestmark = pytest.mark.anyio

ROWS = 300
TOOL = Path(__file__).resolve().parents[2] / "tools" / "state_backup.py"


async def filled(path: Path, rows: int = ROWS) -> StateStore:
    store = await StateStore.open(path, now=lambda: NOW)

    def fill(conn: sqlite3.Connection) -> None:
        for n in range(rows):
            set_setting(conn, f"key{n}", "x" * 500, NOW)

    await store.write(fill)
    return store


def settings(file: Path) -> dict[str, str]:
    with closing(sqlite3.connect(file, autocommit=True)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return dict(conn.execute("SELECT key, value FROM setting").fetchall())


async def test_a_copy_while_the_store_is_open(path: Path, tmp_path: Path) -> None:
    """The newest rows are still in the write-ahead log, where a plain copy would miss them."""
    store = await filled(path)
    assert (path.parent / "state.sqlite-wal").stat().st_size > 0
    plain = tmp_path / "plain.sqlite"
    put(plain, content(path))
    target = tmp_path / "copies" / "one.sqlite"
    report = await asyncio.to_thread(backup, path, target)
    assert (report.path, report.version) == (target, len(load_migrations()))
    assert report.size == target.stat().st_size
    assert len(settings(target)) == ROWS
    with pytest.raises((sqlite3.Error, AssertionError)):
        assert len(settings(plain)) == ROWS
    # One file, private, that needs no other.
    assert [f.name for f in target.parent.iterdir()] == ["one.sqlite"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    await store.aclose()


async def test_a_copy_can_be_opened_as_the_state_file(path: Path, tmp_path: Path) -> None:
    store = await filled(path)
    target = tmp_path / "restored" / "state.sqlite"
    await asyncio.to_thread(backup, path, target)
    await store.aclose()
    store = await StateStore.open(target)
    assert await store.read(lambda conn: get_setting(conn, "key7")) == "x" * 500
    await store.write(lambda conn: set_setting(conn, "new", 1, NOW))
    await store.aclose()


async def test_writes_go_on_during_copies_and_each_copy_is_one_state(
    path: Path, tmp_path: Path
) -> None:
    """Every transaction writes the same number under two keys. A copy never sees two numbers."""
    store = await filled(path)

    def both(n: int, conn: sqlite3.Connection) -> None:
        set_setting(conn, "left", n, NOW)
        set_setting(conn, "right", n, NOW)

    stop = False
    written = 0

    async def writer() -> None:
        nonlocal written
        while not stop:
            written += 1
            await store.write(lambda conn, n=written: both(n, conn))

    await store.write(lambda conn: both(0, conn))
    writing = asyncio.ensure_future(writer())
    seen = []
    try:
        for n in range(12):
            before = written
            target = tmp_path / f"copy{n}.sqlite"
            await asyncio.to_thread(backup, path, target)
            copy = settings(target)
            assert copy["left"] == copy["right"], n
            assert int(copy["left"]) >= before - 1  # what was acknowledged is in it
            seen.append(int(copy["left"]))
    finally:
        stop = True
        await writing
    assert seen == sorted(seen) and written > seen[0]
    assert await store.read(lambda conn: get_setting(conn, "left")) == written
    await store.aclose()


async def test_a_copy_made_by_the_tool_from_another_process(path: Path, tmp_path: Path) -> None:
    store = await filled(path)
    config = tmp_path / "s3t.toml"
    config.write_text(
        '[server]\nlisten_address = "127.0.0.1"\nport = 8740\n[paths]\nenv_file = ".env"\n'
        f'data_dir = "data"\nstate_file = "{path}"\n'
    )
    run = [sys.executable, str(TOOL), "--config", str(config)]
    done = await asyncio.to_thread(subprocess.run, run, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert f"schema version {len(load_migrations())}" in done.stdout
    (copy,) = (path.parent / "backups").iterdir()
    assert len(settings(copy)) == ROWS
    again = [*run, "--to", str(copy)]
    refused = await asyncio.to_thread(subprocess.run, again, capture_output=True, text=True)
    assert refused.returncode == 1 and "nothing is overwritten" in refused.stderr
    await store.aclose()


async def test_a_copy_of_a_file_that_no_one_has_open(path: Path, tmp_path: Path) -> None:
    await (await filled(path)).aclose()
    await asyncio.to_thread(backup, path, tmp_path / "copy.sqlite")
    assert len(settings(tmp_path / "copy.sqlite")) == ROWS
    assert [f.name for f in path.parent.iterdir()] == ["state.sqlite"]


async def test_a_copy_after_a_crash(path: Path, tmp_path: Path) -> None:
    """The process died with transactions in the write-ahead log: they are in the copy."""
    script = textwrap.dedent(
        """
        import asyncio, os, sys
        from datetime import UTC, datetime
        from pathlib import Path
        from s3t.store.settings import set_setting
        from s3t.store.state import StateStore

        async def main():
            store = await StateStore.open(Path(sys.argv[1]))
            await store.write(lambda c: set_setting(c, "a", 1, datetime.now(UTC)))
            os._exit(9)

        asyncio.run(main())
        """
    )
    run = [sys.executable, "-c", script, str(path)]
    died = await asyncio.to_thread(subprocess.run, run)
    assert died.returncode == 9 and (path.parent / "state.sqlite-wal").stat().st_size > 0
    backup(path, tmp_path / "copy.sqlite")
    assert settings(tmp_path / "copy.sqlite") == {"a": "1"}
    store = await StateStore.open(path)
    assert await store.read(lambda conn: get_setting(conn, "a")) == 1
    await store.aclose()


async def test_nothing_is_overwritten_and_nothing_half_made_is_left(
    path: Path, tmp_path: Path
) -> None:
    store = await filled(path, 3)
    target = tmp_path / "copies" / "copy.sqlite"
    target.parent.mkdir()
    target.write_text("mine")
    with pytest.raises(StoreError, match="nothing is overwritten"):
        backup(path, target)
    with pytest.raises(StoreError, match="nothing is overwritten"):
        backup(path, path)
    with pytest.raises(StoreError, match="no state file"):
        backup(tmp_path / "absent.sqlite", tmp_path / "copies" / "b.sqlite")
    other = tmp_path / "other.sqlite"
    with closing(sqlite3.connect(other)) as conn:
        conn.execute("CREATE TABLE t (x)")
    with pytest.raises(StoreError, match="not a state file"):
        backup(other, tmp_path / "copies" / "c.sqlite")
    assert [f.name for f in target.parent.iterdir()] == ["copy.sqlite"]
    assert target.read_text() == "mine"
    await store.aclose()


def test_the_name_of_a_copy() -> None:
    eastern = NOW.astimezone(timezone(timedelta(hours=-5)))
    assert backup_name(Path("/x/state.sqlite"), eastern) == "state-20261125T101500Z.sqlite"
    marked = backup_name(Path("/x/state.sqlite"), NOW, "v0001")
    assert marked == "state-v0001-20261125T101500Z.sqlite"
