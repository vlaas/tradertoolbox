"""The numbered migrations (STO-02): from empty, from the previous version, whole or not at all."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from s3t.instants import to_micros
from s3t.store.migrate import (
    APPLICATION_ID,
    Migration,
    StoreError,
    _apply,
    load_migrations,
    migrate,
    version_of,
)

from .conftest import NOW, STEPS, schema

REAL = load_migrations()
NAMES = [str(m) for m in REAL]


def fill(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO person (id, name) VALUES (1, 'Ada'), (2, 'Grace')")


def test_from_empty(conn: sqlite3.Connection) -> None:
    assert version_of(conn, STEPS) == 0
    assert migrate(conn, STEPS, NOW) == 3
    assert version_of(conn, STEPS) == 3
    rows = conn.execute("SELECT * FROM schema_migration").fetchall()
    assert rows == [(m.version, m.name, m.sha256, to_micros(NOW)) for m in STEPS]
    assert conn.execute("PRAGMA application_id").fetchone() == (APPLICATION_ID,)
    assert migrate(conn, STEPS, NOW) == 3  # nothing left to do
    assert not conn.in_transaction


def test_from_the_previous_version_with_data(conn: sqlite3.Connection) -> None:
    migrate(conn, STEPS[:1], NOW)
    fill(conn)
    migrate(conn, STEPS[:2], NOW)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO note VALUES (7, 2, 'x', 0)")
    migrate(conn, STEPS, NOW)
    assert conn.execute("SELECT * FROM person ORDER BY id").fetchall() == [(1, "Ada"), (2, "Grace")]
    assert conn.execute("SELECT person FROM note").fetchall() == [(2,)]
    # The foreign keys are on again, and still point at the rebuilt table.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO note VALUES (8, 99, 'x', 0)")


def test_step_by_step_gives_the_schema_of_from_empty(conn: sqlite3.Connection) -> None:
    for n in range(1, len(STEPS) + 1):
        migrate(conn, STEPS[:n], NOW)
    with closing(sqlite3.connect(":memory:", autocommit=True)) as whole:
        migrate(whole, STEPS, NOW)
        assert schema(conn) == schema(whole)


@pytest.mark.parametrize("n", range(len(REAL)), ids=NAMES)
def test_every_real_migration_applies_from_the_version_before_it(
    conn: sqlite3.Connection, n: int
) -> None:
    """Holds for each migration file that a later package adds."""
    assert migrate(conn, REAL[:n], NOW) == n
    assert migrate(conn, REAL[: n + 1], NOW) == n + 1
    with closing(sqlite3.connect(":memory:", autocommit=True)) as whole:
        migrate(whole, REAL[: n + 1], NOW)
        assert schema(conn) == schema(whole)


def test_the_real_schema_follows_the_rules(conn: sqlite3.Connection) -> None:
    """STRICT tables; an instant is an INTEGER column named ``_at_us`` (GEN-10)."""
    migrate(conn, REAL, NOW)
    tables = conn.execute("SELECT name, strict FROM pragma_table_list WHERE schema = 'main'")
    own = [(name, strict) for name, strict in tables if not name.startswith("sqlite_")]
    assert {"setting", "schema_migration"} <= {name for name, _ in own}
    for name, strict in own:
        assert strict == 1, name
        for column, kind in conn.execute("SELECT name, type FROM pragma_table_info(?)", (name,)):
            looks_like_a_time = column.endswith(("_at", "_time", "_ts", "_us", "_ms"))
            assert not looks_like_a_time or (column.endswith("_at_us") and kind == "INTEGER"), (
                f"{name}.{column}"
            )


@pytest.mark.parametrize(
    "script",
    [
        "CREATE TABLE a (x INTEGER) STRICT; CREATE TABLE a (x INTEGER) STRICT;",
        "CREATE TABLE a (x INTEGER) STRICT; COMMIT; BEGIN;",
        "CREATE TABLE a (x INTEGER) STRICT; COMMIT;",
        "CREATE TABLE a (x INTEGER) STRICT; ROLLBACK; BEGIN;",
        "CREATE TABLE a (x INTEGER) STRICT; SAVEPOINT s;",
        "CREATE TABLE a (x INTEGER) STRICT; PRAGMA user_version=7;",
        "CREATE TABLE a (x INTEGER) STRICT; ATTACH ':memory:' AS b;",
        "CREATE TABLE a (x INTEGER) STRICT; INSERT INTO note VALUES (1, 99, 'x', 0);",
    ],
)
def test_a_migration_is_applied_whole_or_not_at_all(conn: sqlite3.Connection, script: str) -> None:
    migrate(conn, STEPS[:2], NOW)
    fill(conn)
    before = schema(conn)
    with pytest.raises(StoreError, match="0003_bad"):
        migrate(conn, (*STEPS[:2], Migration(3, "bad", script)), NOW)
    assert schema(conn) == before and version_of(conn, STEPS) == 2
    assert conn.execute("SELECT count(*) FROM person").fetchone() == (2,)
    assert conn.execute("PRAGMA user_version").fetchone() == (0,)
    assert not conn.in_transaction
    assert migrate(conn, STEPS, NOW) == 3  # and the file can go on


def test_the_migrations_before_a_bad_one_stay(conn: sqlite3.Connection) -> None:
    with pytest.raises(StoreError):
        migrate(conn, (*STEPS[:2], Migration(3, "bad", "CREATE")), NOW)
    assert version_of(conn, STEPS) == 2


def test_another_process_migrated_first(tmp_path: Path) -> None:
    """Both saw version 0; the second finds the migration applied and does not repeat it."""
    file = tmp_path / "state.sqlite"
    with (
        closing(sqlite3.connect(file, autocommit=True)) as one,
        closing(sqlite3.connect(file, autocommit=True)) as two,
    ):
        assert version_of(two, STEPS) == 0
        assert migrate(one, STEPS[:1], NOW) == 1
        _apply(two, STEPS[0], NOW)
        assert two.execute("SELECT count(*) FROM schema_migration").fetchone() == (1,)
        assert not two.in_transaction


# ---------------------------------------------------------------- what is refused


def test_a_newer_database(conn: sqlite3.Connection) -> None:
    migrate(conn, STEPS, NOW)
    with pytest.raises(StoreError, match="schema version 3 .* knows 2"):
        migrate(conn, STEPS[:2], NOW)


def test_a_changed_migration(conn: sqlite3.Connection) -> None:
    migrate(conn, STEPS, NOW)
    changed = Migration(2, "notes", STEPS[1].sql + " -- better")
    with pytest.raises(StoreError, match="0002_notes .* never changed"):
        migrate(conn, (STEPS[0], changed, STEPS[2]), NOW)


def test_the_file_of_another_program(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE theirs (x)")
    with pytest.raises(StoreError, match="not a state file"):
        migrate(conn, STEPS, NOW)
    assert schema(conn) == [("theirs", "CREATE TABLE theirs (x)")]


def test_another_application_id(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA application_id=77")
    with pytest.raises(StoreError, match="not a state file"):
        migrate(conn, STEPS, NOW)


def test_a_state_file_without_its_record(conn: sqlite3.Connection) -> None:
    migrate(conn, STEPS, NOW)
    conn.execute("DELETE FROM schema_migration WHERE version = 2")
    with pytest.raises(StoreError, match="gap"):
        version_of(conn, STEPS)
    conn.execute("DELETE FROM schema_migration")
    with pytest.raises(StoreError, match="gap"):
        version_of(conn, STEPS)
    conn.execute("DROP TABLE schema_migration")
    with pytest.raises(StoreError, match="no record"):
        version_of(conn, STEPS)


# ---------------------------------------------------------------- the files


def write(directory: Path, *names: str) -> Path:
    for name in names:
        (directory / name).write_text("SELECT 1;")
    return directory


def test_the_files_are_read_in_order(tmp_path: Path) -> None:
    write(tmp_path, "0002_b.sql", "0001_a.sql", "readme.txt")
    assert [str(m) for m in load_migrations(tmp_path)] == ["0001_a", "0002_b"]


@pytest.mark.parametrize(
    "names",
    [
        ("0001_a.sql", "0003_c.sql"),
        ("0002_b.sql",),
        ("0001_a.sql", "0001_b.sql"),
        ("0001_a.sql", "2_b.sql"),
        ("0001-a.sql",),
        ("0001_A.sql",),
    ],
)
def test_a_gap_a_repeat_or_a_bad_name_is_refused(tmp_path: Path, names: tuple[str, ...]) -> None:
    write(tmp_path, *names)
    with pytest.raises(StoreError):
        load_migrations(tmp_path)
