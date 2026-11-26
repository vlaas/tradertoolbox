"""The password hash and the browser sessions (SEC-01). What they mean is in
``s3t.system.login``.

The token of a session never gets here: the functions take its SHA-256.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from s3t.instants import from_micros, to_micros


@dataclass(frozen=True)
class SessionRow:
    token_sha256: str
    created_at: datetime
    """UTC."""
    expires_at: datetime
    """UTC. The first instant at which it is no session any more."""


def get_password_hash(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT password_hash FROM login").fetchone()
    return None if row is None else row[0]


def set_password_hash(conn: sqlite3.Connection, password_hash: str, at: datetime) -> int:
    """A new password ends every session. Returns how many there were."""
    conn.execute(
        "INSERT INTO login (id, password_hash, changed_at_us) VALUES (1, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET password_hash = excluded.password_hash, "
        "changed_at_us = excluded.changed_at_us",
        (password_hash, to_micros(at)),
    )
    return conn.execute("DELETE FROM session").rowcount


def replace_password_hash(conn: sqlite3.Connection, old: str, new: str) -> None:
    """The same password hashed anew, with other parameters. The sessions stay. Does nothing when
    the password was changed in between."""
    conn.execute("UPDATE login SET password_hash = ? WHERE password_hash = ?", (new, old))


def add_session(conn: sqlite3.Connection, session: SessionRow, password_hash: str) -> bool:
    """Open a session for a password that was checked against this hash. False, and no session,
    when the password was changed since it was checked."""
    added = conn.execute(
        "INSERT INTO session (token_sha256, created_at_us, expires_at_us) "
        "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM login WHERE password_hash = ?)",
        (
            session.token_sha256,
            to_micros(session.created_at),
            to_micros(session.expires_at),
            password_hash,
        ),
    )
    return added.rowcount == 1


def find_session(conn: sqlite3.Connection, token_sha256: str, at: datetime) -> SessionRow | None:
    """The session, if there is one and it has not expired."""
    row = conn.execute(
        "SELECT token_sha256, created_at_us, expires_at_us FROM session "
        "WHERE token_sha256 = ? AND expires_at_us > ?",
        (token_sha256, to_micros(at)),
    ).fetchone()
    return None if row is None else SessionRow(row[0], from_micros(row[1]), from_micros(row[2]))


def renew_session(
    conn: sqlite3.Connection, token_sha256: str, expires_at: datetime, at: datetime
) -> bool:
    """Move the end of a session that has not ended. False when it has."""
    renewed = conn.execute(
        "UPDATE session SET expires_at_us = ? WHERE token_sha256 = ? AND expires_at_us > ?",
        (to_micros(expires_at), token_sha256, to_micros(at)),
    )
    return renewed.rowcount == 1


def delete_session(conn: sqlite3.Connection, token_sha256: str) -> bool:
    """Whether there was one."""
    found = conn.execute("DELETE FROM session WHERE token_sha256 = ?", (token_sha256,))
    return found.rowcount > 0


def delete_expired(conn: sqlite3.Connection, at: datetime) -> int:
    return conn.execute("DELETE FROM session WHERE expires_at_us <= ?", (to_micros(at),)).rowcount
