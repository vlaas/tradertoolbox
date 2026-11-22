"""Dividend schedule, PV and the model forward. TestDividends is ported from `volcalc`
tests/test_forward.py at commit f53be83, with the source's dividend table given as the rows
known on each day (the source filtered it by `known_at`).

Not ported: the tests of the parity fit (TestFitForward, TestSliceForwards, test_pair_prints,
test_slice_inputs, test_fwd_model_share). They come with the fit in WP-1.1."""

from datetime import date

import numpy as np
import pytest

from s3t.pricing.forward import (
    Dividend,
    dividend_schedule,
    model_forward,
    pv_dividends,
    pv_dividends_dr,
)

# The source's DIVS table, by the day each row became known.
SPY_2023_12 = (date(2023, 12, 15), 1.906)  # known from 2023-01-19
SPY_2024_03 = (date(2024, 3, 15), 1.595)  # known from 2024-01-05
SPY_2024_06 = (date(2024, 6, 21), 1.759)  # known from 2024-01-05
GDX_2023_12 = (date(2023, 12, 18), 0.5)  # known from 2023-12-15, annual


def _spy(d: date) -> list[tuple[date, float]]:
    rows = [SPY_2023_12]
    return rows + [SPY_2024_03, SPY_2024_06] if d >= date(2024, 1, 5) else rows


def _rows(s: list[Dividend]) -> list[tuple[date, float, bool]]:
    return [(x.ex_date, x.amount, x.projected) for x in s]


class TestDividends:
    def test_schedule_projection(self):
        # Before the January declaration the March ex-date is a projection of the last dividend.
        d = date(2024, 1, 2)
        s = dividend_schedule(_spy(d), 4, d, date(2024, 3, 28))
        assert _rows(s) == [(date(2024, 3, 15), 1.906, True)]
        # Declared on 2024-01-05: the real amount, no duplicate projection.
        d = date(2024, 1, 5)
        s = dividend_schedule(_spy(d), 4, d, date(2024, 3, 28))
        assert _rows(s) == [(date(2024, 3, 15), 1.595, False)]
        # Expiry before the ex-date: nothing; ex-date on the settle date: included.
        d = date(2024, 1, 2)
        assert dividend_schedule(_spy(d), 4, d, date(2024, 3, 14)) == []
        d = date(2024, 3, 1)
        assert len(dividend_schedule(_spy(d), 4, d, date(2024, 3, 15))) == 1
        # Ex-date on d itself is already out of the price.
        d = date(2023, 12, 15)
        assert dividend_schedule(_spy(d), 4, d, date(2024, 1, 19)) == []
        # LEAPS: two declared, then quarterly projections of the last declared amount.
        d = date(2024, 1, 5)
        s = dividend_schedule(_spy(d), 4, d, date(2025, 1, 17))
        assert [x.projected for x in s] == [False, False, True, True]
        assert [x.ex_date for x in s][2:] == [date(2024, 9, 20), date(2024, 12, 20)]
        assert [x.amount for x in s][2:] == [1.759, 1.759]
        # Annual payer: projected a year after the last one; nothing in a 3-month life.
        assert dividend_schedule([GDX_2023_12], 1, d, date(2024, 4, 19)) == []
        s = dividend_schedule([GDX_2023_12], 1, d, date(2025, 1, 17))
        assert _rows(s) == [(date(2024, 12, 17), 0.5, True)]
        assert dividend_schedule([], 4, d, date(2025, 1, 17)) == []

    def test_pv(self):
        d = date(2024, 1, 5)
        s = dividend_schedule(_spy(d), 4, d, date(2024, 6, 28))
        want = 1.595 * np.exp(-0.05 * 70 / 365) + 1.759 * np.exp(-0.05 * 168 / 365)
        assert pv_dividends(s, d, 0.05) == pytest.approx(want)
        assert pv_dividends([], d, 0.05) == 0.0


def test_unknown_frequency_is_quarterly_and_input_order_is_free():
    d = date(2024, 1, 5)
    known = list(reversed(_spy(d)))
    assert dividend_schedule(known, None, d, date(2025, 1, 17)) == dividend_schedule(
        _spy(d), 4, d, date(2025, 1, 17)
    )


def test_pv_rate_derivative():
    d = date(2024, 1, 5)
    s = dividend_schedule(_spy(d), 4, d, date(2025, 1, 17))
    h = 1e-6
    fd = (pv_dividends(s, d, 0.05 + h) - pv_dividends(s, d, 0.05 - h)) / (2 * h)
    assert pv_dividends_dr(s, d, 0.05) == pytest.approx(fd, rel=1e-8)
    assert pv_dividends_dr([], d, 0.05) == 0.0


def test_model_forward():
    assert model_forward(100.0, 0.05, 0.5, 2.0) == pytest.approx(98.0 * np.exp(0.025))
    F = model_forward(np.array([100.0, 50.0]), 0.0, 1.0, np.array([0.0, 1.0]))
    assert F.tolist() == [100.0, 49.0]
