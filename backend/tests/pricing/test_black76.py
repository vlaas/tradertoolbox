"""Port of `volcalc` tests/test_black76.py at commit f53be83: round trip (hypothesis), fail
codes, forward Greeks against finite differences, clock invariance of w = sigma^2 tau.

Changed in the port: TestPyVollib is not ported (py-vollib brings numba and pandas; the
independent cross-check is test_greeks_mpmath.py). The SLV tick schedule of the removed
bsm.tick_size is written out as `_penny_tick`. Added: TestAddedGreeks for theta, rho, vanna,
charm and volga."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from s3t.pricing.black76 import (
    FAIL_ABOVE_CAP,
    FAIL_BELOW_INTRINSIC,
    FAIL_INVALID,
    FAIL_NAMES,
    FAIL_OK,
    FAIL_UNIDENTIFIABLE,
    IV_CAP,
    IV_TOL,
    RESID_TOL,
    black_greeks,
    black_iv,
    black_price,
)
from s3t.pricing.bsm import _MIN_SIGMA, _MIN_T, bsm_price


def _penny_tick(price):
    """The penny programme's tick: $0.01 under $3 of premium, $0.05 from $3 (SLV in volcalc)."""
    return np.where(np.asarray(price) < 3.0, 0.01, 0.05)


def _arr(*xs):
    return tuple(np.asarray([x], dtype=np.float64) for x in xs)


def _extrinsic(price, F, K, tau, df, is_call):
    return price - black_price(F, K, tau, df, np.full_like(price, _MIN_SIGMA), is_call)


class TestRoundTrip:
    @settings(max_examples=1000, deadline=None)
    @given(
        logF=st.floats(np.log(5.0), np.log(6000.0)),
        k=st.floats(-1.0, 1.0),
        w=st.floats(1e-6, 4.0),
        tau=st.floats(_MIN_T, 2.5),
        df=st.floats(0.85, 1.0),
        is_call=st.booleans(),
    )
    def test_price_iv_price(self, logF, k, w, tau, df, is_call):
        """Spec §9.1: price 1e-8 * F, IV 1e-6 for w in [1e-6, 4] on identifiable prices."""
        sigma = np.sqrt(w / tau)
        if not (_MIN_SIGMA < sigma < IV_CAP):
            return  # IV > IV_CAP is nulled by design (above_cap); at the cap rounding decides
        F, K, tau_, df_, sig = _arr(np.exp(logF), np.exp(logF + k), tau, df, sigma)
        c = np.array([is_call])
        price = black_price(F, K, tau_, df_, sig, c)
        if _extrinsic(price, F, K, tau_, df_, c)[0] <= 0.02 + 1e-9:
            return  # the gate nulls these by design
        res = black_iv(price, F, K, tau_, df_, c)
        assert res.fail[0] == FAIL_OK, FAIL_NAMES[res.fail[0]]
        back = black_price(F, K, tau_, df_, res.iv, c)
        assert abs(back[0] - price[0]) <= RESID_TOL * F[0]
        assert abs(res.iv[0] - sigma) <= IV_TOL

    def test_batch_round_trip_with_ticks(self):
        g = np.random.default_rng(5)
        n = 50_000
        F = g.uniform(20.0, 600.0, n)
        K = F * np.exp(g.normal(0.0, 0.15, n))
        tau = np.exp(g.uniform(np.log(_MIN_T * 5), np.log(1.0), n))
        df = np.exp(-0.05 * tau)
        sigma = g.uniform(0.05, 1.5, n)
        c = g.random(n) < 0.5
        price = black_price(F, K, tau, df, sigma, c)
        res = black_iv(price, F, K, tau, df, c, tick=_penny_tick(price))
        ok = res.fail == FAIL_OK
        ext = _extrinsic(price, F, K, tau, df, c)
        gate = np.maximum(0.02, _penny_tick(price) / 2)
        assert np.array_equal(ok, ext > gate)  # the only failures are gated rows
        assert set(np.unique(res.fail[~ok])) == {FAIL_UNIDENTIFIABLE}
        back = black_price(F[ok], K[ok], tau[ok], df[ok], res.iv[ok], c[ok])
        assert (np.abs(back - price[ok]) <= RESID_TOL * F[ok]).all()
        assert (np.abs(res.iv[ok] - sigma[ok]) <= IV_TOL).all()
        assert np.isnan(res.iv[~ok]).all()


class TestFailCodes:
    def test_each_code(self):
        F, K, tau, df = _arr(100.0, 100.0, 0.25, 0.99)
        c = np.array([True])
        fair = black_price(F, K, tau, df, np.array([0.2]), c)
        cases = {
            FAIL_OK: (fair, F, K, tau, df),
            FAIL_BELOW_INTRINSIC: (np.array([0.5]), np.array([150.0]), K, tau, df),  # ITM < 50
            FAIL_ABOVE_CAP: (np.array([99.5]), F, K, tau, df),  # above DF * F
            FAIL_UNIDENTIFIABLE: (np.array([49.51]), np.array([150.0]), K, tau, df),
            FAIL_INVALID: (fair, np.array([np.nan]), K, tau, df),
        }
        for code, (p, f, k, t, d) in cases.items():
            res = black_iv(p, f, k, t, d, c)
            assert res.fail[0] == code, (FAIL_NAMES[code], FAIL_NAMES[res.fail[0]])
            assert np.isnan(res.iv[0]) == (code != FAIL_OK)

    def test_iv_cap(self):
        # 0DTE ATM at $50 premium: the solver finds sigma > 5 -> nulled as above_cap.
        F, K, tau, df = _arr(480.0, 480.0, 390 / 525600, 1.0)
        res = black_iv(np.array([50.0]), F, K, tau, df, np.array([True]))
        assert res.fail[0] == FAIL_ABOVE_CAP and np.isnan(res.iv[0])
        # Just under the cap stays.
        p = black_price(F, K, tau, df, np.array([IV_CAP - 0.1]), np.array([True]))
        assert black_iv(p, F, K, tau, df, np.array([True])).fail[0] == FAIL_OK

    def test_invalid_inputs_do_not_poison_others(self):
        F = np.array([100.0, -1.0, 100.0, 100.0])
        K = np.array([100.0, 100.0, 100.0, 100.0])
        tau = np.array([0.5, 0.5, np.inf, 0.5])
        df = np.array([0.98, 0.98, 0.98, 0.0])
        c = np.ones(4, dtype=bool)
        with np.errstate(all="ignore"):
            price = black_price(F, K, tau, df, np.full(4, 0.3), c)
        with np.errstate(all="raise"):
            res = black_iv(price, F, K, tau, df, c)
        assert res.fail.tolist() == [FAIL_OK, FAIL_INVALID, FAIL_INVALID, FAIL_INVALID]
        assert res.iv[0] == pytest.approx(0.3, abs=1e-9)

    def test_tick_widens_gate(self):
        F, K, tau, df = _arr(100.0, 80.0, 7 / 365, 0.999)
        c = np.array([True])
        price = black_price(F, K, tau, df, np.array([_MIN_SIGMA]), c) + 0.04  # extrinsic $0.04
        assert black_iv(price, F, K, tau, df, c).fail[0] == FAIL_OK
        assert black_iv(price, F, K, tau, df, c, tick=np.array([0.1])).fail[0] == (
            FAIL_UNIDENTIFIABLE
        )


class TestGreeksAndParity:
    def test_black_equals_bsm_on_forward(self):
        F, K, tau, df, sig = _arr(470.0, 465.0, 30 / 365, 0.996, 0.14)
        r = -np.log(df) / tau
        for c in (np.array([True]), np.array([False])):
            assert black_price(F, K, tau, df, sig, c)[0] == pytest.approx(
                bsm_price(F, K, tau, r, r, sig, c)[0], rel=1e-14
            )

    def test_put_call_parity(self):
        F, K, tau, df, sig = _arr(100.0, 95.0, 0.5, 0.97, 0.3)
        C = black_price(F, K, tau, df, sig, np.array([True]))[0]
        P = black_price(F, K, tau, df, sig, np.array([False]))[0]
        assert C - P == pytest.approx(df[0] * (F[0] - K[0]), abs=1e-12)

    def test_forward_greeks_vs_finite_differences(self):
        F, K, tau, df, sig = _arr(100.0, 105.0, 0.5, 0.97, 0.3)
        for c in (np.array([True]), np.array([False])):
            g = black_greeks(F, K, tau, df, sig, c)
            h = 1e-4
            dF = (
                black_price(F + h, K, tau, df, sig, c) - black_price(F - h, K, tau, df, sig, c)
            ) / (2 * h)
            d2F = (
                black_price(F + h, K, tau, df, sig, c)
                - 2 * black_price(F, K, tau, df, sig, c)
                + black_price(F - h, K, tau, df, sig, c)
            ) / h**2
            dS = (
                black_price(F, K, tau, df, sig + h, c) - black_price(F, K, tau, df, sig - h, c)
            ) / (2 * h)
            assert g["delta"][0] == pytest.approx(dF[0], abs=1e-7)
            assert g["gamma"][0] == pytest.approx(d2F[0], abs=1e-5)
            assert g["vega"][0] == pytest.approx(dS[0], abs=1e-7)
        g_c = black_greeks(F, K, tau, df, sig, np.array([True]))
        g_p = black_greeks(F, K, tau, df, sig, np.array([False]))
        assert g_c["delta"][0] - g_p["delta"][0] == pytest.approx(df[0], abs=1e-12)
        assert 0 < g_c["delta"][0] < df[0] and -df[0] < g_p["delta"][0] < 0


class TestClockInvariance:
    def test_w_is_clock_free(self):
        """Spec §9.1: inverting the same price on tau_cal and tau_bus gives the same
        w = sigma^2 tau to 1e-10 (the price fixes w; the clock only relabels sigma)."""
        g = np.random.default_rng(3)
        n = 2000
        F = g.uniform(50.0, 600.0, n)
        K = F * np.exp(g.normal(0.0, 0.05, n))
        tau_cal = g.uniform(0.01, 0.3, n)
        tau_bus = tau_cal * g.uniform(0.5, 1.3, n)  # another clock's label of the same horizon
        df = np.exp(-0.05 * tau_cal)
        w = g.uniform(1e-4, 0.02, n)  # sigma stays under IV_CAP on both clocks
        c = g.random(n) < 0.5
        price = black_price(F, K, tau_cal, df, np.sqrt(w / tau_cal), c)
        # Away from the gate margin: the ported floor is Black(sigma = 0.001), which itself
        # moves by ~0.4 * F * 0.001 * sqrt(tau) with the clock.
        ext = np.minimum(
            _extrinsic(price, F, K, tau_cal, df, c), _extrinsic(price, F, K, tau_bus, df, c)
        )
        keep = ext > 0.2
        F, K, tau_cal, tau_bus, df, w, c, price = (
            x[keep] for x in (F, K, tau_cal, tau_bus, df, w, c, price)
        )
        n = keep.sum()
        a = black_iv(price, F, K, tau_cal, df, c)
        b = black_iv(price, F, K, tau_bus, df, c)
        ok = (a.fail == FAIL_OK) & (b.fail == FAIL_OK)
        assert n > 1000 and ok.sum() == n
        assert np.abs(a.iv[ok] ** 2 * tau_cal[ok] - b.iv[ok] ** 2 * tau_bus[ok]).max() < 1e-10
        assert np.abs(a.iv[ok] ** 2 * tau_cal[ok] - w[ok]).max() < 1e-10


class TestAddedGreeks:
    """theta, rho, vanna, charm and volga against central differences of black_price, with F
    fixed and DF = e^{-r tau}; theta and charm per year of calendar time."""

    @staticmethod
    def _price(F, K, tau, r, sig, c):
        return black_price(F, K, tau, np.exp(-r * tau), sig, c)

    @pytest.mark.parametrize(
        ("F", "K", "tau", "r", "sig"),
        [
            (100.0, 105.0, 0.5, 0.03, 0.3),
            (4800.0, 4500.0, 30 / 365, 0.045, 0.18),
            (50.0, 50.0, 2.0, 0.0, 0.6),
            (480.0, 500.0, 3 / 365, 0.05, 0.12),
        ],
    )
    def test_against_finite_differences(self, F, K, tau, r, sig):
        F, K, tau, r, sig = _arr(F, K, tau, r, sig)
        for c in (np.array([True]), np.array([False])):
            g = black_greeks(F, K, tau, np.exp(-r * tau), sig, c)
            p = self._price
            h_t, h_s, h_r, h_f = 1e-6, 1e-5, 1e-6, 1e-6 * F
            theta = (p(F, K, tau - h_t, r, sig, c) - p(F, K, tau + h_t, r, sig, c)) / (2 * h_t)
            rho = (p(F, K, tau, r + h_r, sig, c) - p(F, K, tau, r - h_r, sig, c)) / (2 * h_r)
            volga = (
                p(F, K, tau, r, sig + h_s, c) - 2 * p(F, K, tau, r, sig, c)
                + p(F, K, tau, r, sig - h_s, c)
            ) / h_s**2  # fmt: skip

            def delta(F_, tau_, sig_, c=c):
                return black_greeks(F_, K, tau_, np.exp(-r * tau_), sig_, c)["delta"]

            vanna = (delta(F, tau, sig + h_s) - delta(F, tau, sig - h_s)) / (2 * h_s)
            charm = (delta(F, tau - h_t, sig) - delta(F, tau + h_t, sig)) / (2 * h_t)
            vanna_f = (
                black_greeks(F + h_f, K, tau, np.exp(-r * tau), sig, c)["vega"]
                - black_greeks(F - h_f, K, tau, np.exp(-r * tau), sig, c)["vega"]
            ) / (2 * h_f)
            scale = F[0]
            assert g["theta"][0] == pytest.approx(theta[0], rel=1e-5, abs=1e-6 * scale)
            assert g["rho"][0] == pytest.approx(rho[0], rel=1e-6, abs=1e-8 * scale)
            assert g["volga"][0] == pytest.approx(volga[0], rel=1e-3, abs=1e-5 * scale)
            assert g["vanna"][0] == pytest.approx(vanna[0], rel=1e-6, abs=1e-9)
            assert g["vanna"][0] == pytest.approx(vanna_f[0], rel=1e-6, abs=1e-9)
            assert g["charm"][0] == pytest.approx(charm[0], rel=1e-5, abs=1e-6)

    def test_call_put_relations(self):
        """Parity C - P = DF (F - K) fixes the put's Greeks from the call's."""
        F, K, tau, df, sig = _arr(100.0, 95.0, 0.5, 0.97, 0.3)
        g_c = black_greeks(F, K, tau, df, sig, np.array([True]))
        g_p = black_greeks(F, K, tau, df, sig, np.array([False]))
        r = -np.log(df[0]) / tau[0]
        for k in ("gamma", "vega", "vanna", "volga"):
            assert g_c[k][0] == pytest.approx(g_p[k][0], rel=1e-12)
        fwd = df[0] * (F[0] - K[0])  # value of the forward contract
        assert g_c["theta"][0] - g_p["theta"][0] == pytest.approx(r * fwd, rel=1e-10)
        assert g_c["rho"][0] - g_p["rho"][0] == pytest.approx(-tau[0] * fwd, rel=1e-10)
        assert g_c["charm"][0] - g_p["charm"][0] == pytest.approx(r * df[0], rel=1e-10)
