"""The application's settings: values the user changes in the app, as JSON under a key.

They are not the configuration file. That one holds what belongs to the machine (GEN-08).
"""

import json
import sqlite3
from datetime import datetime
from typing import Any

from s3t.instants import to_micros


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return default if row is None else json.loads(row[0])


def set_setting(conn: sqlite3.Connection, key: str, value: Any, at: datetime) -> None:
    """``value`` is anything JSON can hold; a number that is not finite is refused."""
    conn.execute(
        "INSERT INTO setting (key, value, updated_at_us) VALUES (?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
        "updated_at_us = excluded.updated_at_us",
        (key, json.dumps(value, allow_nan=False), to_micros(at)),
    )


def delete_setting(conn: sqlite3.Connection, key: str) -> bool:
    """Whether there was one."""
    return conn.execute("DELETE FROM setting WHERE key = ?", (key,)).rowcount > 0


def all_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM setting ORDER BY key")
    return {key: json.loads(value) for key, value in rows}
