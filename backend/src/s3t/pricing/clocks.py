"""The calendar clock: time to expiry in years (REF-05).

Source: `volcalc` src/vrp/iv/clocks.py at commit f53be83: `tau_cal` and, as `tau_cal_array`,
`tau_cal_expr` on numpy arrays instead of polars columns.

From the source docstring: the clock runs to the settlement instant T, not to "expiry 16:00",
and is anchored at the quote's timestamp. tau_cal = calendar minutes to T / minutes per year
(525,600, a 365-day year; bt_dataset divided by 525,960), floored at one minute. IV is inverted
on this clock.

Changed in the port (2026-11-22, WP-0.2):
- The year defaults to MINUTES_PER_YEAR (product definition 11.1); the source took it from its
  configuration.
- `tau_cal` refuses a naive datetime: its timestamp would depend on the machine's time zone.
- Not ported: `add_clocks` and `tau_bus` (the trading-time clock: it needs the exchange
  calendar of WP-0.6, and the product definition makes it a later option), and `tau_var` (the
  variance clock of `volcalc`'s realised-volatility study).

Which instant is a contract's settlement instant (PM or AM settlement, early closes) is decided
by the caller from the instrument; this module measures the time to it.
"""

from datetime import datetime

import numpy as np

MINUTES_PER_YEAR = 525_600  # 365 days
_NS_PER_MIN = 60e9


def tau_cal(ts: datetime, settle: datetime, minutes_per_year: int = MINUTES_PER_YEAR) -> float:
    """Years on the calendar clock from `ts` to `settle`, both time-zone aware; at least one
    minute. An epoch difference, so a clock change inside the horizon counts real minutes."""
    if ts.tzinfo is None or settle.tzinfo is None:
        raise ValueError("tau_cal needs time-zone aware datetimes")
    return max((settle.timestamp() - ts.timestamp()) / 60.0, 1.0) / minutes_per_year


def tau_cal_array(
    ts: np.ndarray, settle: np.ndarray, minutes_per_year: int = MINUTES_PER_YEAR
) -> np.ndarray:
    """Array form of tau_cal. `ts` and `settle` are datetime64 arrays in UTC (numpy has no time
    zones), of any unit, broadcastable; NaN where either is NaT; at least one minute."""
    a = np.asarray(ts).astype("datetime64[ns]")
    b = np.asarray(settle).astype("datetime64[ns]")
    minutes = (b.astype(np.int64) - a.astype(np.int64)).astype(np.float64) / _NS_PER_MIN
    out = np.maximum(minutes, 1.0) / minutes_per_year
    return np.where(np.isnat(a) | np.isnat(b), np.nan, out)
