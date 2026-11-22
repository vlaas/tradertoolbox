"""Black-Scholes with discrete dividends (s3t/pricing/escrowed.py): spot Greeks, parity, IV.

TestSpotGreeks is `volcalc`'s TestBsmGreeks (tests/test_bsm.py at commit f53be83) moved from
the removed bsm_greeks to escrowed_greeks without dividends, where both describe the same
option. The rest is new in WP-0.2."""

import numpy as np
import pytest

from s3t.pricing.black76 import FAIL_INVALID, FAIL_OK, GREEKS
from s3t.pricing.bsm import bsm_price
from s3t.pricing.escrowed import escrowed_greeks, escrowed_iv, escrowed_price

# ATM call: S=100, K=100, T=1, r=0.05, no dividends, sigma=0.20
_S = np.array([100.0])
_K = np.array([100.0])
_T = np.array([1.0])
_r = np.array([0.05])
_sig = np.array([0.20])
_call = np.array([True])
_put = np.array([False])


def _g(S=_S, K=_K, T=_T, r=_r, sig=_sig, c=_call):
    return escrowed_greeks(S, K, T, r, 0.0, 0.0, sig, c)


class TestSpotGreeks:
    def test_ranges_and_signs(self):
        g = _g()
        assert 0.0 < g["delta"][0] < 1.0
        assert g["gamma"][0] > 0 and g["vega"][0] > 0 and g["theta"][0] < 0
        assert -1.0 < _g(c=_put)["delta"][0] < 0.0

    def test_atm_call_delta_near_half(self):
        assert 0.45 < _g(r=np.array([0.0]))["delta"][0] < 0.65

    def test_call_put_gamma_vega_equal_and_delta_parity(self):
        g_call, g_put = _g(), _g(c=_put)
        assert g_call["gamma"][0] == pytest.approx(g_put["gamma"][0], rel=1e-8)
        assert g_call["vega"][0] == pytest.approx(g_put["vega"][0], rel=1e-8)
        assert g_call["delta"][0] - g_put["delta"][0] == pytest.approx(1.0, abs=1e-8)

    def test_zero_dte_greeks_finite(self):
        g = _g(T=np.array([0.0]))
        for k in GREEKS:
            assert np.isfinite(g[k][0]), k
        assert g["gamma"][0] >= 0 and g["vega"][0] >= 0


def test_no_dividends_is_bsm():
    g = np.random.default_rng(8)
    n = 2000
    S = g.uniform(5.0, 600.0, n)
    K = S * np.exp(g.normal(0.0, 0.2, n))
    T = g.uniform(1 / 365, 2.0, n)
    r = g.uniform(-0.01, 0.08, n)
    sig = g.uniform(0.05, 1.5, n)
    c = g.random(n) < 0.5
    ours = escrowed_price(S, K, T, r, 0.0, sig, c)
    theirs = bsm_price(S, K, T, r, np.zeros(n), sig, c)
    assert np.allclose(ours, theirs, rtol=1e-12, atol=1e-12 * S)


def test_put_call_parity_with_dividends():
    S, K, T, r, sig = 100.0, 95.0, 0.5, 0.04, 0.3
    pv = 1.2 * np.exp(-r * 0.1) + 1.2 * np.exp(-r * 0.35)
    C = escrowed_price(S, K, T, r, pv, sig, True)
    P = escrowed_price(S, K, T, r, pv, sig, False)
    assert C - P == pytest.approx(S - pv - K * np.exp(-r * T), abs=1e-12)


class TestGreeksWithDividends:
    """Every Greek against central differences of the price, with the dividends as cash
    amounts at fixed dates: time passing moves tau and every time to an ex-date together."""

    T_DIV = np.array([0.05, 0.30, 0.55])
    AMOUNT = np.array([0.9, 0.9, 0.95])

    def _pv(self, r, elapsed=0.0):
        t = self.T_DIV - elapsed
        live = t > 0
        return float(np.sum(self.AMOUNT[live] * np.exp(-r * t[live])))

    def _dpv_dr(self, r):
        return float(-np.sum(self.AMOUNT * self.T_DIV * np.exp(-r * self.T_DIV)))

    def _price(self, S, K, tau, r, sig, c, elapsed=0.0):
        return float(escrowed_price(S, K, tau - elapsed, r, self._pv(r, elapsed), sig, c))

    def _delta(self, S, K, tau, r, sig, c, elapsed=0.0):
        pv, dpv = self._pv(r, elapsed), self._dpv_dr(r)
        return float(escrowed_greeks(S, K, tau - elapsed, r, pv, dpv, sig, c)["delta"])

    @pytest.mark.parametrize(
        ("S", "K", "tau", "r", "sig"),
        [
            (100.0, 105.0, 0.75, 0.03, 0.3),
            (450.0, 430.0, 0.6, 0.045, 0.16),
            (40.0, 40.0, 1.5, 0.0, 0.55),
            (200.0, 260.0, 0.9, 0.06, 0.4),
        ],
    )
    def test_against_finite_differences(self, S, K, tau, r, sig):
        for c in (True, False):
            args = (
                np.array([S]),
                np.array([K]),
                np.array([tau]),
                np.array([r]),
                self._pv(r),
                self._dpv_dr(r),
                np.array([sig]),
                np.array([c]),
            )
            g = {k: v[0] for k, v in escrowed_greeks(*args).items()}
            p, d = self._price, self._delta
            hS, hs, hr, ht = 1e-3 * S, 1e-5, 1e-6, 1e-6
            fd = {
                "delta": (p(S + hS, K, tau, r, sig, c) - p(S - hS, K, tau, r, sig, c)) / (2 * hS),
                "gamma": (
                    p(S + hS, K, tau, r, sig, c)
                    - 2 * p(S, K, tau, r, sig, c)
                    + p(S - hS, K, tau, r, sig, c)
                )
                / hS**2,
                "theta": (
                    p(S, K, tau, r, sig, c, elapsed=ht) - p(S, K, tau, r, sig, c, elapsed=-ht)
                )
                / (2 * ht),
                "vega": (p(S, K, tau, r, sig + hs, c) - p(S, K, tau, r, sig - hs, c)) / (2 * hs),
                "rho": (p(S, K, tau, r + hr, sig, c) - p(S, K, tau, r - hr, sig, c)) / (2 * hr),
                "vanna": (d(S, K, tau, r, sig + hs, c) - d(S, K, tau, r, sig - hs, c)) / (2 * hs),
                "charm": (
                    d(S, K, tau, r, sig, c, elapsed=ht) - d(S, K, tau, r, sig, c, elapsed=-ht)
                )
                / (2 * ht),
                "volga": (
                    p(S, K, tau, r, sig + 1e-4, c)
                    - 2 * p(S, K, tau, r, sig, c)
                    + p(S, K, tau, r, sig - 1e-4, c)
                )
                / 1e-8,
            }
            for k in GREEKS:
                assert g[k] == pytest.approx(fd[k], rel=1e-4, abs=1e-6 * S), (k, c)

    def test_dividends_lower_calls_raise_puts(self):
        base = escrowed_price(100.0, 100.0, 0.75, 0.03, 0.0, 0.3, np.array([True, False]))
        div = escrowed_price(100.0, 100.0, 0.75, 0.03, self._pv(0.03), 0.3, np.array([True, False]))
        assert div[0] < base[0] and div[1] > base[1]


class TestIV:
    def test_round_trip_with_dividends(self):
        g = np.random.default_rng(9)
        n = 5000
        S = g.uniform(20.0, 600.0, n)
        K = S * np.exp(g.normal(0.0, 0.12, n))
        T = g.uniform(7 / 365, 1.5, n)
        r = g.uniform(0.0, 0.06, n)
        pv = S * g.uniform(0.0, 0.03, n)
        sig = g.uniform(0.08, 1.2, n)
        c = g.random(n) < 0.5
        price = escrowed_price(S, K, T, r, pv, sig, c)
        res = escrowed_iv(price, S, K, T, r, pv, c)
        ok = res.fail == FAIL_OK
        assert ok.mean() > 0.9  # the rest are gated deep in- or out-of-the-money rows
        assert np.abs(res.iv[ok] - sig[ok]).max() < 1e-6

    def test_dividends_above_spot_are_invalid(self):
        res = escrowed_iv(np.array([5.0]), 100.0, 100.0, 0.5, 0.03, 100.0, np.array([True]))
        assert res.fail[0] == FAIL_INVALID and np.isnan(res.iv[0])
