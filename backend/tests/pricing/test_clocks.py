"""The calendar clock (REF-05). test_tau_cal_cases is ported from `volcalc` tests/test_clocks.py
at commit f53be83 and run on both forms, the scalar and the array one; the cases are minutes
from a quote to a settlement instant, so 0DTE, early closes, the one-minute floor and a clock
change are covered.

Not ported: test_add_clocks, test_mixed_time_units, test_add_clocks_keeps_row_order_and_columns
and test_tau_var, with the functions they test."""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from s3t.pricing.clocks import MINUTES_PER_YEAR, tau_cal, tau_cal_array

ET = ZoneInfo("America/New_York")
MPY = 525_600


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=ET)


def _utc64(s: str) -> np.datetime64:
    return np.datetime64(_dt(s).astimezone(ZoneInfo("UTC")).replace(tzinfo=None), "ns")


PAIRS = [
    ("2024-01-03 09:30:00", "2024-01-03 16:00:00"),  # winter 0DTE at the open: 390
    ("2024-07-05 09:30:00", "2024-07-05 16:00:00"),  # summer (EDT): still 390
    ("2024-01-03 09:35:00", "2024-01-04 16:00:00"),  # 1 DTE: 1825
    ("2024-11-29 09:30:00", "2024-11-29 13:00:00"),  # early close: 210
    ("2024-01-03 15:59:30", "2024-01-03 16:00:00"),  # 30 s left -> floored at 1 minute
    ("2024-01-03 16:05:00", "2024-01-03 16:00:00"),  # after the close -> 1 minute
    ("2024-03-08 15:00:00", "2024-03-11 16:00:00"),  # across the DST switch: calendar minutes
]
WANT = [390, 390, 1825, 210, 1, 1, 3 * 1440 + 60 - 60]  # Fri 15:00 -> Mon 16:00 = 4380 min


def test_tau_cal_cases():
    for (a, b), m in zip(PAIRS, WANT, strict=True):
        assert tau_cal(_dt(a), _dt(b), MPY) * MPY == pytest.approx(m, abs=1e-9)
    # The year: 390 minutes of a 525,600-minute year; bt_dataset's 525,960 is not used.
    assert MINUTES_PER_YEAR == MPY
    assert tau_cal(_dt(PAIRS[0][0]), _dt(PAIRS[0][1])) == pytest.approx(390 / 525_600)


def test_tau_cal_array_matches_scalar():
    ts = np.array([_utc64(a) for a, _ in PAIRS])
    settle = np.array([_utc64(b) for _, b in PAIRS])
    got = tau_cal_array(ts, settle) * MPY
    assert got == pytest.approx(WANT, abs=1e-9)
    # Coarser units are converted, and NaT gives NaN instead of a number.
    s_units = tau_cal_array(ts.astype("datetime64[s]"), settle.astype("datetime64[s]"))
    assert s_units * MPY == pytest.approx(WANT, abs=1e-9)
    nat = tau_cal_array(ts[:2], np.array([settle[0], np.datetime64("NaT", "ns")]))
    assert nat[0] * MPY == pytest.approx(390) and np.isnan(nat[1])


def test_fall_back_counts_the_extra_hour():
    # Fri 10:00 EDT -> Mon 16:00 EST: 3 days, 6 hours and the repeated hour of 3 November.
    got = tau_cal(_dt("2024-11-01 10:00:00"), _dt("2024-11-04 16:00:00")) * MPY
    assert got == pytest.approx(3 * 1440 + 6 * 60 + 60)


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError):
        tau_cal(datetime(2024, 1, 3, 9, 30), _dt("2024-01-03 16:00:00"))
