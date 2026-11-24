"""The instant an option settles at (REF-05, MKT-01).

**Proposed 2026-11-24 (WP-0.5); the owner decides.** The product definition, 11.1, takes the
instant from the broker's ``expires-at`` and settlement type. ``expires-at`` is not that
instant for stock and ETF options: for every one of them in the captures of `tt` (133 files) it
is 15 minutes after the close (16:15 New York, 13:15 on an early close), and `tt` found by
measurement that the platform itself counts to 16:00. For SPXW and XSP it is the close, and for
an AM-settled SPX option the open (live, 2026-11-23). So the rule here is:

- **Equity and index options:** PM-settled at the close of the regular session on the
  expiration date, AM-settled at its open. The session comes from the exchange calendar, so a
  holiday or an early close needs no rule of its own. It is also what `volcalc` does, whose
  methods are the default (product definition, 11.1).
- **Futures options:** the broker's ``expires_at``, which a futures chain states per
  expiration.

The broker's own instants stay in the records as facts (``expires_at``, ``stops_trading_at``).

The calendar arrives with WP-0.6. Until then nothing implements :class:`SessionTimes` in the
product: a stand-in without early closes would be three hours wrong on such a day, and no
package uses the instant before WP-1.1.
"""

from datetime import date, datetime
from typing import Protocol

from s3t.broker.instruments import Expiration, OptionContract


class SessionTimes(Protocol):
    """The regular session of US equity and index options: the part of the exchange calendar
    (REF-04, WP-0.6) that the settlement rule needs."""

    def session(self, day: date) -> tuple[datetime, datetime] | None:
        """The open and the close of the regular session of a date, in UTC; None on a day
        without a session."""
        ...


def settles_at(option: Expiration | OptionContract, sessions: SessionTimes) -> datetime | None:
    """The settlement instant in UTC; None when there is no session on the expiration date,
    or when a futures option states no instant."""
    if option.family == "future":
        return option.expires_at
    session = sessions.session(option.expiration_date)
    if session is None:
        return None
    opens, closes = session
    return opens if option.settlement == "am" else closes
