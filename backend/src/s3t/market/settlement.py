"""The instant an option settles at (REF-05, MKT-01). Decided 2026-11-24 (owner, WP-0.5 review).

The broker's ``expires-at`` is not that instant for stock and ETF options: for every one of them
in the captures of `tt` (133 files) it is 15 minutes after the close (16:15 New York, 13:15 on an
early close), and `tt` found by measurement on SPY that the platform itself counts to 16:00. For
SPXW and XSP it is the close, and for an AM-settled SPX option the open (live, 2026-11-23). So
the rule here is:

- **Equity and index options:** PM-settled at the close of the regular session on the
  expiration date, AM-settled at its open. The session comes from the exchange calendar, so an
  early close needs no rule of its own.
- **An expiration date without a session** (a closure declared after the option was listed, as on
  2025-01-09) settles with the session before it, as `volcalc` has it. `volcalc` knows PM only;
  for AM the same is assumed.
- **Futures options:** the broker's ``expires_at``, which a futures chain states per
  expiration.

A date that the calendar does not cover gives None, never the calendar's last session. The
calendar must therefore reach the last listed expiration.

The broker's own instants stay in the records as facts (``expires_at``, ``stops_trading_at``).

The calendar arrives with WP-0.6. Until then nothing implements :class:`SessionTimes` in the
product: a stand-in without early closes would be three hours wrong on such a day, and no
package uses the instant before WP-1.1.
"""

from datetime import date, datetime, timedelta
from typing import Protocol

from s3t.broker.instruments import Expiration, OptionContract

DAYS_BACK = 4
"""How far back a session is looked for: a long weekend and one day more (`volcalc`)."""


class SessionTimes(Protocol):
    """The regular session of US equity and index options: the part of the exchange calendar
    (REF-04, WP-0.6) that the settlement rule needs."""

    def covers(self, day: date) -> bool:
        """Whether the calendar knows this date, with or without a session."""
        ...

    def session(self, day: date) -> tuple[datetime, datetime] | None:
        """The open and the close of the regular session of a covered date, in UTC; None on a
        day without a session."""
        ...


def settles_at(option: Expiration | OptionContract, sessions: SessionTimes) -> datetime | None:
    """The settlement instant in UTC. None when it is not known: the calendar does not cover
    the expiration date, no session lies near it, or a futures option states no instant."""
    if option.family == "future":
        return option.expires_at
    if not sessions.covers(option.expiration_date):
        return None
    for back in range(DAYS_BACK + 1):
        day = option.expiration_date - timedelta(days=back)
        session = sessions.session(day) if sessions.covers(day) else None
        if session is not None:
            opens, closes = session
            return opens if option.settlement == "am" else closes
    return None
