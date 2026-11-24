"""The exchange schedules of the `exchange-calendars` library, as plain values.

The only module that imports the library, and with it pandas. The import is inside the function:
it takes long enough to hold up the event loop, so the calendar is built in a thread at the start
of the backend (``s3t.refdata.calendar.load_calendar``). Nothing of pandas leaves this module.
"""

from datetime import UTC, date, datetime
from importlib.metadata import version
from typing import Any

PACKAGE = "exchange-calendars"

type Schedule = dict[date, tuple[datetime, datetime]]
"""Trading day to the open and the close of its regular session, in UTC."""


def schedule(exchange: str, first: date, last: date) -> Schedule:
    """The sessions of an exchange (``XNYS``, ``CMES``) from ``first`` to ``last``."""
    from exchange_calendars import get_calendar

    table = get_calendar(exchange, start=first.isoformat(), end=last.isoformat()).schedule
    return {
        day.date(): (_plain(opens), _plain(closes))
        for day, opens, closes in table[["open", "close"]].itertuples()
    }


def _plain(instant: Any) -> datetime:
    """A ``datetime`` in ``datetime.UTC``; the library has its own UTC object."""
    return instant.to_pydatetime().astimezone(UTC)


def source() -> str:
    """The package and its version: holidays declared later are known to a newer one only."""
    return f"{PACKAGE} {version(PACKAGE)}"
