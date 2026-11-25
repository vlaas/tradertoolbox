"""The login tables (SEC-01) and the migration that brings them, the first one that is applied to
a state file with content."""

import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from s3t.store.login import (
    SessionRow,
    add_session,
    delete_expired,
    delete_session,
    find_session,
    get_password_hash,
    replace_password_hash,
    set_password_hash,
)
from s3t.store.migrate import load_migrations, migrate
from s3t.store.settings import get_setting, set_setting
from s3t.store.state import StateStore

from .conftest import NOW

HASH, OTHER = "$argon2id$v=19$one", "$argon2id$v=19$two"
DAY = timedelta(days=1)


def row(letter: str, days: int = 30) -> SessionRow:
    return SessionRow(letter * 64, NOW, NOW + days * DAY)


@pytest.fixture
def login(conn: sqlite3.Connection) -> sqlite3.Connection:
    migrate(conn, load_migrations(), NOW)
    set_password_hash(conn, HASH, NOW)
    return conn


@pytest.mark.anyio
async def test_the_migration_of_a_file_with_content(path: Path) -> None:
    store = await StateStore.open(path, migrations=load_migrations()[:1], now=lambda: NOW)
    await store.write(lambda conn: set_setting(conn, "zone", "UTC", NOW))
    await store.aclose()

    store = await StateStore.open(path, now=lambda: NOW)
    assert await store.read(lambda conn: get_setting(conn, "zone")) == "UTC"
    assert await store.read(get_password_hash) is None  # nobody has set one yet
    await store.aclose()
    copy = path.parent / "backups" / "state-v0001-20261125T101500Z.sqlite"
    with closing(sqlite3.connect(copy)) as conn:
        assert conn.execute("SELECT max(version) FROM schema_migration").fetchone() == (1,)
        assert get_setting(conn, "zone") == "UTC"


def test_one_password(login: sqlite3.Connection) -> None:
    assert get_password_hash(login) == HASH
    set_password_hash(login, OTHER, NOW)
    assert get_password_hash(login) == OTHER
    assert login.execute("SELECT count(*) FROM login").fetchone() == (1,)
    with pytest.raises(sqlite3.IntegrityError):
        login.execute("INSERT INTO login VALUES (2, ?, 0)", (HASH,))


def test_only_an_argon2id_hash_is_taken(login: sqlite3.Connection) -> None:
    """A password by mistake, or a hash of another kind."""
    for wrong in ("a password", "$argon2i$v=19$x", "$2b$12$x", ""):
        with pytest.raises(sqlite3.IntegrityError):
            set_password_hash(login, wrong, NOW)


def test_a_new_password_ends_every_session(login: sqlite3.Connection) -> None:
    for letter in "ab":
        assert add_session(login, row(letter), HASH)
    assert set_password_hash(login, OTHER, NOW) == 2
    assert find_session(login, "a" * 64, NOW) is None


def test_the_same_password_hashed_anew_keeps_them(login: sqlite3.Connection) -> None:
    assert add_session(login, row("a"), HASH)
    replace_password_hash(login, HASH, OTHER)
    assert get_password_hash(login) == OTHER and find_session(login, "a" * 64, NOW) == row("a")
    replace_password_hash(login, HASH, "$argon2id$late")  # changed in between
    assert get_password_hash(login) == OTHER


def test_no_session_for_a_password_that_was_changed(login: sqlite3.Connection) -> None:
    """The password was right when it was checked, which takes a while, and was changed then."""
    set_password_hash(login, OTHER, NOW)
    assert not add_session(login, row("a"), HASH)
    assert login.execute("SELECT count(*) FROM session").fetchone() == (0,)


def test_a_session_ends(login: sqlite3.Connection) -> None:
    assert add_session(login, row("a", days=1), HASH) and add_session(login, row("b"), HASH)
    last = NOW + DAY - timedelta(microseconds=1)
    assert find_session(login, "a" * 64, last) == row("a", days=1)
    assert find_session(login, "a" * 64, NOW + DAY) is None
    assert delete_expired(login, NOW + DAY) == 1
    assert delete_session(login, "b" * 64) and not delete_session(login, "b" * 64)
    assert find_session(login, "b" * 64, NOW) is None


def test_the_hash_of_a_token_and_nothing_else(login: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        add_session(login, SessionRow("a-token", NOW, NOW + DAY), HASH)
    with pytest.raises(sqlite3.IntegrityError):
        add_session(login, SessionRow("a" * 64, NOW, NOW), HASH)
