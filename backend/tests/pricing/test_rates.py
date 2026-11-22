"""Rate conversions and the curve. test_continuous_compounding_values and test_interpolate_rate
are ported from `volcalc` tests/test_refdata.py at commit f53be83 (the conversion now takes
floats, not a polars expression). The RateCurve tests are new.

Not ported: test_rates_asof_never_looks_ahead and test_build_rates_local_precedence_and_known_at,
with the functions they test (WP-0.6)."""

import math

import numpy as np
import pytest

from s3t.pricing import rates
from s3t.pricing.rates import RateCurve


def _value(series: str, y: float) -> float:
    return float(rates.to_continuous(series, y))


def test_continuous_compounding_values():
    y = 0.05
    assert _value("DGS1", y) == pytest.approx(2 * math.log(1.025), abs=1e-15)
    assert _value("DGS1", y) == pytest.approx(0.0493852252, abs=1e-10)
    # each conversion reproduces the instrument's own growth / price over its tenor
    for s, n in (("DGS1MO", 30), ("DGS3MO", 91), ("DGS6MO", 182)):
        assert math.exp(_value(s, y) * n / 365) == pytest.approx(1 + y * n / 365, rel=1e-14)
    assert math.exp(_value("SOFR", y) / 365) == pytest.approx(1 + y / 360, rel=1e-14)
    assert math.exp(-_value("DTB3", y) * 91 / 365) == pytest.approx(1 - y * 91 / 360, rel=1e-14)
    # continuous < simple for every positive quote except discount/act-360 ones
    assert _value("DGS3MO", y) < y < _value("SOFR", y) < _value("DTB3", y)


def test_interpolate_rate():
    r = {"SOFR": 0.05, "DGS1MO": 0.04, "DGS3MO": None, "DGS6MO": 0.06}
    assert rates.interpolate_rate(r, 0.2) == 0.05  # clamped below the first knot
    assert rates.interpolate_rate(r, 15.5) == pytest.approx(0.045)
    assert rates.interpolate_rate(r, 106) == pytest.approx(0.05)  # 30 -> 182 skips null 3M
    assert rates.interpolate_rate(r, 400) == 0.06
    np.testing.assert_allclose(rates.interpolate_rate(r, np.array([1.0, 30.0])), [0.05, 0.04])
    with pytest.raises(ValueError):
        rates.interpolate_rate({"DGS1": None}, 10)


class TestRateCurve:
    QUOTES = {"SOFR": 0.05, "DGS1MO": 0.04, "DGS3MO": None, "DGS6MO": 0.06}

    def test_curve_agrees_with_interpolate_rate(self):
        curve = RateCurve.from_rates(self.QUOTES)
        assert curve.source == "curve"
        tau = np.array([0.2, 15.5, 106.0, 400.0]) / 365
        want = rates.interpolate_rate(self.QUOTES, tau * 365)
        np.testing.assert_allclose(curve.rate(tau), want, rtol=1e-15)
        np.testing.assert_allclose(curve.discount(tau), np.exp(-want * tau), rtol=1e-15)

    def test_flat_fallback(self):
        curve = RateCurve.flat(0.0293)
        assert curve.source == "flat"
        assert curve.rate(np.array([0.001, 1.0, 30.0])).tolist() == [0.0293] * 3
        assert float(curve.discount(2.0)) == pytest.approx(math.exp(-0.0586))

    def test_invalid_curves_are_refused(self):
        with pytest.raises(ValueError):
            RateCurve.from_rates({"SOFR": None})
        with pytest.raises(ValueError):
            RateCurve((30.0, 30.0), (0.04, 0.05), "curve")
        with pytest.raises(ValueError):
            RateCurve((), (), "flat")

    def test_curve_is_a_value(self):
        curve = RateCurve.flat(0.04)
        with pytest.raises(AttributeError):
            curve.rates = (0.05,)  # type: ignore[misc]
        assert hash(curve) == hash(RateCurve.flat(0.04))
