"""The state file and the transactions on it (STO-01, STO-02).

One thread owns the connection, so a slow disk never holds up the event loop. A function that
works on the tables takes the connection and is run in one transaction:

    value = await store.read(lambda conn: get_setting(conn, "zone"))
    await store.write(lambda conn: set_setting(conn, "zone", "UTC", now))

An error in the function rolls the transaction back. A cancelled ``await`` does not stop a
transaction that has begun: it is committed, and the caller is not told.

WAL mode, so that a backup reads while a write goes on. ``synchronous=FULL``: an acknowledged
write survives a loss of power, and writes are rare here (decided 2026-11-25).

Opening applies the migrations that are missing. Before it changes a file that has content, it
makes a copy (``s3t.store.backup``). A file that cannot be used is refused before anything in it
is changed, its journal mode included.
"""

import asyncio
import logging
import os
import sqlite3
import stat
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from s3t.store.backup import BUSY_MS, backup, copy_path
from s3t.store.migrate import Migration, StoreError, load_migrations, migrate, version_of

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoreStatus:
    """For the health route (SEC-08)."""

    version: int
    """The schema version."""
    size: int
    """Bytes of the state file, without what the write-ahead log still holds."""
    sqlite: str


class StateStore:
    """Open it with :meth:`open`."""

    def __init__(
        self, path: Path, conn: sqlite3.Connection, executor: ThreadPoolExecutor, version: int
    ) -> None:
        self._path, self._conn, self._executor = path, conn, executor
        self._version = version

    def __repr__(self) -> str:
        return f"StateStore(version={self._version})"

    @classmethod
    async def open(
        cls,
        path: Path,
        *,
        backup_dir: Path | None = None,
        migrations: Sequence[Migration] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> "StateStore":
        """Open the state file, create it if there is none, and apply the missing migrations.
        The copy made before a migration goes to ``backup_dir``, by default ``backups`` beside
        the state file. Raises :class:`StoreError` for a file that cannot be used."""
        known = load_migrations() if migrations is None else migrations
        copies = path.parent / "backups" if backup_dir is None else backup_dir
        executor = ThreadPoolExecutor(1, thread_name_prefix="s3t-store")
        loop = asyncio.get_running_loop()
        try:
            conn, version = await loop.run_in_executor(executor, _open, path, copies, known, now)
        except BaseException:
            executor.shutdown(wait=False)
            raise
        return cls(path, conn, executor, version)

    async def read[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``fn`` in a transaction that cannot write."""
        return await self._run(self._read, fn)

    async def write[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``fn`` in a transaction, and commit it when ``fn`` returns."""
        return await self._run(self._write, fn)

    async def status(self) -> StoreStatus:
        size = await self._run(lambda: self._path.stat().st_size)
        return StoreStatus(self._version, size, sqlite3.sqlite_version)

    async def aclose(self) -> None:
        """Wait for the transactions under way and close the file."""
        await self._run(self._conn.close)
        self._executor.shutdown()

    # ------------------------------------------------------------------ on the store's thread

    async def _run[**P, T](self, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: fn(*args, **kwargs))

    def _read[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        self._conn.execute("PRAGMA query_only=ON")
        try:
            return self._transaction("BEGIN", fn)
        finally:
            self._conn.execute("PRAGMA query_only=OFF")

    def _write[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return self._transaction("BEGIN IMMEDIATE", fn)

    def _transaction[T](self, begin: str, fn: Callable[[sqlite3.Connection], T]) -> T:
        conn = self._conn
        conn.execute(begin)
        try:
            result = fn(conn)
            if not conn.in_transaction:
                raise StoreError("a function on the store ended its own transaction")
            conn.execute("COMMIT")
            return result
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")


def _open(
    path: Path,
    copies: Path,
    migrations: Sequence[Migration],
    now: Callable[[], datetime],
) -> tuple[sqlite3.Connection, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    _create_private(path)
    conn = sqlite3.connect(path, autocommit=True)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_MS}")
        # Refuse an unusable file first: WAL mode stays in a file's header.
        version = version_of(conn, migrations)
        if 0 < version < len(migrations):
            report = backup(path, copy_path(copies, path, now(), f"v{version:04d}"))
            LOG.info("state store: copied to %s before its migration", report.path.name)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        reached = migrate(conn, migrations, now())
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
    except BaseException:
        conn.close()
        raise
    if reached != version:
        LOG.info("state store: migrated from schema version %d to %d", version, reached)
    return conn, reached


def _create_private(path: Path) -> None:
    """Create the file readable by the owner only. SQLite gives its side files the same mode."""
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600))
    except FileExistsError:
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            LOG.warning("state file %s is open to group or others; use chmod 600", path.name)
