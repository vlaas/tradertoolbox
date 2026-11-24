"""A consistent copy of the state file, also while the backend runs (STO-07).

A plain copy of the file is not a backup. In WAL mode the newest transactions are in the file
beside it, and a checkpoint can change the file while it is copied. SQLite's online backup reads
one consistent state in one step. Writers are not held up by it.

The copy is one file that needs no other. It is checked before it gets its name, so a file with
that name is whole. Nothing is overwritten, and nothing is deleted. To use a copy, stop the
backend and put the copy in the place of the state file, and remove the state file's ``-wal`` and
``-shm`` files.
"""

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from s3t.store.migrate import APPLICATION_ID, StoreError

BUSY_MS = 5_000


@dataclass(frozen=True)
class BackupReport:
    path: Path
    size: int
    version: int
    """The schema version of the copy."""


def backup_name(state_file: Path, at: datetime, mark: str = "") -> str:
    """``state-20261125T101500Z.sqlite``; with a mark, ``state-v0001-20261125T101500Z.sqlite``.
    The ending keeps the copy out of the repository (a test refuses it there)."""
    parts = [state_file.stem, mark, f"{at.astimezone(UTC):%Y%m%dT%H%M%SZ}"]
    return "-".join(p for p in parts if p) + ".sqlite"


def backup(state_file: Path, target: Path) -> BackupReport:
    """Copy the state file to ``target``, which must not exist. Blocking."""
    if target.exists():
        raise StoreError(f"backup: {target} exists already, and nothing is overwritten")
    if not state_file.is_file():
        raise StoreError(f"backup: no state file at {state_file}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.stem}.{os.getpid()}.partial.sqlite")
    # Readable by the owner only, from the first byte.
    os.close(os.open(partial, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600))
    try:
        version = _copy(state_file, partial)
        with partial.open("rb+") as f:
            os.fsync(f.fileno())
        size = partial.stat().st_size
        os.replace(partial, target)
    except sqlite3.Error as e:
        raise StoreError(f"backup: {e}") from e
    finally:
        partial.unlink(missing_ok=True)
    return BackupReport(target, size, version)


def _copy(state_file: Path, partial: Path) -> int:
    # rw and query_only, not ro: a read-only connection to a WAL database that no one else has
    # open leaves its -wal and -shm files behind.
    uri = f"{state_file.resolve().as_uri()}?mode=rw"
    with (
        closing(sqlite3.connect(uri, uri=True, autocommit=True)) as source,
        closing(sqlite3.connect(partial, autocommit=True)) as copy,
    ):
        source.execute(f"PRAGMA busy_timeout={BUSY_MS}")
        source.execute("PRAGMA query_only=ON")
        if source.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise StoreError("backup: not a state file of this application")
        source.backup(copy)  # every page in one step
        # The copy inherits WAL mode from the header; without it the copy is one file.
        copy.execute("PRAGMA journal_mode=DELETE")
        result = copy.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise StoreError("backup: the copy failed SQLite's integrity check")
        return copy.execute("SELECT max(version) FROM schema_migration").fetchone()[0]
