"""Numbered migration files and how they are applied (STO-02).

A migration is ``NNNN_name.sql`` in ``s3t/store/migrations``, numbered from 0001 without a gap.
Each runs in one transaction, together with the row in ``schema_migration`` that records it, so
a migration is applied whole or not at all. The row holds the checksum of the file. A database
is refused when

- it is not a state file of this application (``application_id``, and the table);
- it was written by a newer version of the application: there are no down-migrations, and the
  way back is the copy made before the migration (``s3t.store.state``);
- a migration that it has applied is not the file that is here now.

A script cannot end the transaction it runs in, change a pragma or attach another file: an
authorizer denies it while the script runs. A check afterwards would come too late, because
``COMMIT; BEGIN`` leaves a transaction open. Foreign keys are off during a migration, so that a
table can be rebuilt, and are checked before the commit.
"""

import hashlib
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from importlib.resources.abc import Traversable

from s3t.instants import to_micros

APPLICATION_ID = int.from_bytes(b"S3T1")
"""In the header of every state file, so that another program's SQLite file is not taken for one."""

_FILE = re.compile(r"(\d{4})_([a-z0-9_]+)\.sql")
_DENIED = {
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_PRAGMA,
}
_TABLE = """
CREATE TABLE schema_migration (
    version INTEGER NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    applied_at_us INTEGER NOT NULL
) STRICT
"""


class StoreError(Exception):
    """The state file cannot be used as it is. The message names no content of it."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()

    def __str__(self) -> str:
        return f"{self.version:04d}_{self.name}"


def load_migrations(source: Traversable | None = None) -> tuple[Migration, ...]:
    """The migrations in order. ``source`` is a directory; by default the package's own."""
    directory = files("s3t.store") / "migrations" if source is None else source
    found = []
    for entry in directory.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        m = _FILE.fullmatch(entry.name)
        if m is None:
            raise StoreError(f"migration {entry.name}: the name is not NNNN_name.sql")
        found.append(Migration(int(m[1]), m[2], entry.read_text(encoding="utf-8")))
    found.sort(key=lambda m: m.version)
    if [m.version for m in found] != list(range(1, len(found) + 1)):
        raise StoreError("the migrations are not numbered from 0001 without a gap or a repeat")
    return tuple(found)


def version_of(conn: sqlite3.Connection, migrations: Sequence[Migration]) -> int:
    """The schema version of a database, 0 for an empty one. Raises :class:`StoreError` for a
    database that this application cannot use. Changes nothing."""
    try:
        application = _pragma(conn, "application_id")
        objects = conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0]
    except sqlite3.DatabaseError as e:
        raise StoreError(f"not a readable SQLite file ({e})") from None
    if application == 0 and objects == 0:
        return 0
    if application != APPLICATION_ID:
        raise StoreError("a SQLite file, but not a state file of this application")
    applied = _applied(conn)
    if [version for version, _, _ in applied] != list(range(1, len(applied) + 1)) or not applied:
        raise StoreError("the record of the applied migrations has a gap")
    if len(applied) > len(migrations):
        raise StoreError(
            f"the state file has schema version {len(applied)} and this version of the "
            f"application knows {len(migrations)}; use the newer application, or a copy of the "
            "state file from before its migration"
        )
    for (version, name, sha256), migration in zip(applied, migrations, strict=False):
        if sha256 != migration.sha256:
            raise StoreError(
                f"migration {version:04d}_{name} was applied to the state file and is not the "
                f"file that is here now ({migration}); a migration is never changed"
            )
    return len(applied)


def migrate(conn: sqlite3.Connection, migrations: Sequence[Migration], now: datetime) -> int:
    """Apply what is missing and return the schema version. ``conn`` has ``autocommit=True``."""
    version = version_of(conn, migrations)
    if version == len(migrations):
        return version
    # Without effect inside a transaction, so it comes before BEGIN.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for migration in migrations[version:]:
            _apply(conn, migration, now)
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    return len(migrations)


def _apply(conn: sqlite3.Connection, migration: Migration, now: datetime) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _pragma(conn, "application_id") != APPLICATION_ID:
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
            conn.execute(_TABLE)
        # Another process may have migrated the file since it was looked at.
        if any(version == migration.version for version, _, _ in _applied(conn)):
            conn.execute("ROLLBACK")
            return
        conn.set_authorizer(_authorize)
        try:
            conn.executescript(migration.sql)
        finally:
            conn.set_authorizer(None)
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise StoreError(f"migration {migration}: it leaves a foreign key without its row")
        conn.execute(
            "INSERT INTO schema_migration VALUES (?, ?, ?, ?)",
            (migration.version, migration.name, migration.sha256, to_micros(now)),
        )
        conn.execute("COMMIT")
    except sqlite3.Error as e:
        raise StoreError(f"migration {migration}: {e}") from e
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _authorize(action: int, name: str | None, *_: str | None) -> int:
    # SQLite checks the rows itself when a column with a CHECK is added, with this pragma.
    if action == sqlite3.SQLITE_PRAGMA and name == "quick_check":
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY if action in _DENIED else sqlite3.SQLITE_OK


def _applied(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    try:
        return conn.execute(
            "SELECT version, name, sha256 FROM schema_migration ORDER BY version"
        ).fetchall()
    except sqlite3.OperationalError:
        raise StoreError("the state file has no record of its migrations") from None


def _pragma(conn: sqlite3.Connection, name: str) -> int:
    return conn.execute(f"PRAGMA {name}").fetchone()[0]
