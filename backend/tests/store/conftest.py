"""Fixtures for the state store: a file in a temporary directory, and migration lists of the
test's own."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from s3t.store.migrate import Migration

NOW = datetime(2026, 11, 25, 10, 15, tzinfo=UTC)

STEPS = (
    Migration(
        1,
        "people",
        "CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT NOT NULL) STRICT;",
    ),
    Migration(
        2,
        "notes",
        "CREATE TABLE note (id INTEGER PRIMARY KEY, person INTEGER NOT NULL "
        "REFERENCES person(id), text TEXT NOT NULL, made_at_us INTEGER NOT NULL) STRICT;"
        "ALTER TABLE person ADD COLUMN age INTEGER CHECK (age >= 0);",
    ),
    # A table that others refer to is rebuilt: possible only with the foreign keys off.
    Migration(
        3,
        "people_without_age",
        "CREATE TABLE person_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL CHECK (name <> ''))"
        " STRICT;"
        "INSERT INTO person_new SELECT id, name FROM person;"
        "DROP TABLE person;"
        "ALTER TABLE person_new RENAME TO person;",
    ),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "state.sqlite"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(":memory:", autocommit=True)) as connection:
        yield connection


def content(file: Path) -> bytes:
    return file.read_bytes()


def put(file: Path, content: bytes) -> None:
    file.parent.mkdir(exist_ok=True)
    file.write_bytes(content)


def schema(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Every object of the schema with its text, without differences of spacing and quoting."""
    rows = conn.execute("SELECT name, sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY name")
    return [(name, " ".join(sql.replace('"', "").split())) for name, sql in rows]
