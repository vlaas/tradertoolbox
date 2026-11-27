"""The parity forward. TestFitForward and TestParityForward are ported from `volcalc`
tests/test_forward.py at commit f53be83 (TestFitForward, TestSliceForwards), on arrays of one
expiration and on quote mids.

Not ported: the tests of the free-discount-factor fit, of the pairing of prints and of the
transport to the snapshot spot, which were not ported either."""

import numpy as np
import pytest

from s3t.pricing import parity
from s3t.pricing.parity import MODEL, NONE, PARITY, fit_forward, mid, parity_forward

S, TAU, R = 468.0, 0.0384, 0.054
DF = float(np.exp(-R * TAU))


def _model(pv: float = 0.0) -> float:
    return (S - pv) * float(np.exp(R * TAU))


def _chain(forward: float, strikes, put: float = 3.0):
    """Exact parity pairs: C - P = DF (F - K)."""
    k = np.asarray(strikes, dtype=np.float64)
    return k, put + DF * (forward - k), np.full(k.shape, put)


class TestFitForward:
    def test_exact_chain(self):
        F, D = 469.123456, 0.9979
        K = np.arange(450.0, 480.0, 2.0)
        fit = fit_forward(K, D * (F - K), D)
        assert fit.pairs == len(K)
        assert fit.forward == pytest.approx(F, abs=1e-6)
        assert fit.rmse == pytest.approx(0.0, abs=1e-9)

    def test_weights(self):
        """Weighted mean of the inliers: two pairs at F + 0.02 and F - 0.02 with weights 3:1."""
        fit = fit_forward([98.0, 100.0, 102.0], [2.02, 0.0, -2.02], 1.0, [3.0, 1.0, 1.0])
        assert fit.forward == pytest.approx(100.0 + (3 * 0.02 - 0.02) / 5)

    def test_nothing_to_fit(self):
        assert fit_forward([], [], 1.0).pairs == 0
        assert np.isnan(fit_forward([100.0, 101.0], [0.5, -0.5], 1.0).forward)


class TestParityForward:
    STRIKES = (455.0, 460.0, 465.0, 470.0, 475.0, 480.0)

    def test_recovers_the_forward(self):
        F = _model() * (1 + 7e-4)
        out = parity_forward(*_chain(F, self.STRIKES), DF, spot=S, model=_model())
        assert out.source == PARITY and out.pairs == out.offered == 6
        assert out.forward == out.parity == pytest.approx(F, abs=1e-6)
        assert out.rmse == pytest.approx(0.0, abs=1e-9)
        assert out.div_implied == pytest.approx(S - F * DF, abs=1e-6)
        assert out.div_flag is None  # no dividend in the slice's life

    def test_outlier_screened(self):
        K, C, P = _chain(_model(), self.STRIKES)
        C[2] += 1.0  # a call quote one dollar off
        out = parity_forward(K, C, P, DF, spot=S, model=_model())
        assert out.offered == 6 and out.pairs == 5
        assert out.parity == pytest.approx(_model(), abs=1e-6)

    def test_fewer_than_three_pairs_is_model(self):
        model = _model(1.5)
        out = parity_forward(*_chain(model, (465.0, 470.0)), DF, spot=S, model=model, pv_div=1.5)
        assert out.source == MODEL and out.forward == model
        assert out.offered == 2 and np.isnan(out.parity) and out.div_flag is None
        empty = parity_forward([], [], [], DF, spot=S, model=model, pv_div=1.5)
        assert empty.source == MODEL and empty.offered == 0

    def test_band_and_exdiv_exclusion(self):
        strikes = (440.0, 460.0, 465.0, 470.0, 475.0, 500.0)  # 440 and 500 are > 5 % away
        chain = _chain(_model(), strikes)
        out = parity_forward(*chain, DF, spot=S, model=_model())
        assert out.offered == 4 and out.parity == pytest.approx(_model(), abs=1e-6)
        near = parity_forward(*chain, DF, spot=S, model=_model(), exdiv_near=True)
        assert near.offered == 2 and near.source == MODEL  # ITM calls K < F dropped

    def test_implied_dividend_flag(self):
        pv = 1.5
        for pv_market, flag in ((1.55, False), (2.0, True)):
            F = (S - pv_market) * float(np.exp(R * TAU))
            chain = _chain(F, (460.0, 465.0, 470.0, 475.0))
            out = parity_forward(*chain, DF, spot=S, model=_model(pv), pv_div=pv)
            assert out.div_implied == pytest.approx(pv_market, abs=1e-6)
            assert out.div_flag is flag


class TestWithoutASpot:
    """New in WP-1.1: an index has no usable quote (MKT-05), and may have no trade yet."""

    def test_the_band_is_centred_on_the_nearest_pair(self):
        F = 6012.34
        strikes = np.arange(5000.0, 7000.0, 25.0)
        out = parity_forward(*_chain(F, strikes, put=40.0), DF)
        assert out.source == PARITY and out.forward == pytest.approx(F, abs=1e-6)
        inside = np.abs(strikes / F - 1) < parity.FWD_BAND
        assert out.offered == int(inside.sum()) < strikes.size
        assert np.isnan(out.div_implied) and out.div_flag is None

    def test_no_pairs_no_forward(self):
        out = parity_forward([100.0, 105.0], [np.nan, 2.0], [1.0, np.nan], DF)
        assert out.source == NONE and np.isnan(out.forward) and out.offered == 0

    def test_missing_sides_are_skipped(self):
        K, C, P = _chain(470.0, self_strikes := (460.0, 465.0, 470.0, 475.0, 480.0))
        C[0], P[4] = np.nan, np.nan
        out = parity_forward(K, C, P, DF)
        assert out.offered == len(self_strikes) - 2 and out.forward == pytest.approx(470.0)


def test_mid():
    bid = [1.0, 0.0, np.nan, 2.0, 1.0, 0.05]
    ask = [1.2, 0.1, 1.0, np.nan, 0.9, 0.05]
    out = mid(bid, ask)
    assert out[0] == pytest.approx(1.1) and out[5] == pytest.approx(0.05)
    assert np.isnan(out[1:5]).all()  # no bid, a missing side, a crossed quote
