"""Port of `volcalc` tests/test_bsm.py at commit f53be83 (itself a port of bt_dataset
tests/test_greeks.py) for s3t/pricing/bsm.py: BSM kernels, IV solver, gate.

Not ported, with the functions they test: TestBsmGreeks (bsm_greeks; the same checks run on the
spot Greeks in test_escrowed.py), test_enforce_iv_greeks_contract and test_tick_schedule."""

import itertools

import numpy as np
import pytest

from s3t.pricing.bsm import (
    _MIN_SIGMA,
    bsm_extrinsic,
    bsm_price,
    compute_iv,
    iv_unidentifiable_mask,
)

# ATM call: S=100, K=100, T=1, r=0.05, q=0, sigma=0.20
_S = np.array([100.0])
_K = np.array([100.0])
_T = np.array([1.0])
_r = np.array([0.05])
_q = np.array([0.0])
_sig = np.array([0.20])
_call = np.array([True])
_put = np.array([False])


class TestBsmPrice:
    def test_atm_call_approx(self):
        # Known BSM value for S=100, K=100, T=1, r=5%, q=0, sigma=20% ~ 10.45
        price = bsm_price(_S, _K, _T, _r, _q, _sig, _call)
        assert price[0] == pytest.approx(10.45, abs=0.05)

    def test_put_call_parity(self):
        C = bsm_price(_S, _K, _T, _r, _q, _sig, _call)[0]
        P = bsm_price(_S, _K, _T, _r, _q, _sig, _put)[0]
        forward_value = _S[0] * np.exp(-_q[0] * _T[0]) - _K[0] * np.exp(-_r[0] * _T[0])
        assert (C - P) == pytest.approx(forward_value, abs=1e-6)

    def test_deep_itm_call_approaches_intrinsic(self):
        S = np.array([200.0])
        price = bsm_price(S, _K, _T, _r, _q, np.array([0.01]), _call)
        intrinsic = (S - _K * np.exp(-_r * _T))[0]
        assert price[0] == pytest.approx(intrinsic, abs=0.5)

    def test_deep_otm_call_near_zero(self):
        assert bsm_price(_S, np.array([300.0]), _T, _r, _q, _sig, _call)[0] < 0.01

    def test_vectorized_monotone_in_strike(self):
        n = 100
        prices = bsm_price(
            np.full(n, 100.0),
            np.linspace(80, 120, n),
            np.full(n, 1.0),
            np.full(n, 0.05),
            np.zeros(n),
            np.full(n, 0.20),
            np.ones(n, dtype=bool),
        )
        assert (np.diff(prices) < 0).all()

    def test_zero_time_floors(self):
        assert bsm_price(_S, _K, np.array([0.0]), _r, _q, _sig, _call)[0] >= 0


class TestComputeIV:
    def test_round_trips(self):
        for sigma_true, flag in ((0.25, _call), (0.30, _put)):
            price = bsm_price(_S, _K, _T, _r, _q, np.array([sigma_true]), flag)
            assert compute_iv(price, _S, _K, _T, _r, _q, flag)[0] == pytest.approx(
                sigma_true, abs=1e-4
            )

    def test_below_intrinsic_is_nan(self):
        iv = compute_iv(np.array([0.001]), np.array([150.0]), _K, _T, _r, _q, _call)
        assert np.isnan(iv[0])

    def test_vectorized_iv(self):
        n = 20
        S, K = np.full(n, 100.0), np.linspace(85, 115, n)
        T, r, q = np.full(n, 0.5), np.full(n, 0.05), np.zeros(n)
        is_call = np.ones(n, dtype=bool)
        prices = bsm_price(S, K, T, r, q, np.full(n, 0.25), is_call)
        iv = compute_iv(prices, S, K, T, r, q, is_call)
        valid = ~np.isnan(iv)
        assert valid.sum() > n * 0.8
        assert iv[valid].mean() == pytest.approx(0.25, abs=0.02)

    def test_solver_bounds(self):
        S = np.array([100.0, 100.0, 100.0])
        K = np.array([95.0, 100.0, 105.0])
        T, r, q = np.full(3, 0.25), np.full(3, 0.05), np.zeros(3)
        is_call = np.ones(3, dtype=bool)
        prices = bsm_price(S, K, T, r, q, np.array([0.15, 0.20, 0.25]), is_call)
        iv = compute_iv(prices, S, K, T, r, q, is_call)
        assert (iv[~np.isnan(iv)] > 0).all() and (iv[~np.isnan(iv)] < 5.0).all()


class TestIVBracketFilter:
    """Rows outside [BSM(MIN_SIGMA), BSM(MAX_SIGMA)] are NaN and never emit the
    bisection-didn't-converge warning (trades below intrinsic are unphysical)."""

    def test_below_bracket_returns_nan(self):
        # Deep-ITM 0DTE call at 9:30 ET: BSM(MIN_SIGMA) ~ 50; $49 is below the bracket.
        iv = compute_iv(np.array([49.0]), np.array([150.0]), _K, np.array([7.42e-4]), _r, _q, _call)
        assert np.isnan(iv[0])

    def test_no_warning_on_unphysical_trades(self, caplog):
        S, K = np.array([150.0, 150.0]), np.array([100.0, 100.0])
        T, r, q = np.array([0.1, 7.42e-4]), np.array([0.05, 0.05]), np.zeros(2)
        is_call = np.array([True, True])
        price = np.array([51.0, 49.0])  # 1st physical, 2nd below intrinsic
        with caplog.at_level("WARNING", logger="s3t.pricing.bsm"):
            iv = compute_iv(price, S, K, T, r, q, is_call)
        assert not np.isnan(iv[0]) and np.isnan(iv[1])
        assert not [r for r in caplog.records if "bisection" in r.getMessage().lower()]


def _fmt_cells(label, combos, idx, iv_hat, metric, time_value=None):
    lines = [label + ":"]
    for i in idx:
        s, m, T, c = combos[i]
        line = (
            f"  sigma={s} S/K={m} T={T:.4f} {'C' if c else 'P'} "
            f"iv_hat={iv_hat[i]:.6f} metric={metric[i]:.3e}"
        )
        if time_value is not None:
            line += f" time_value={time_value[i]:.4f}"
        lines.append(line)
    return "\n".join(lines)


class TestBSMIVRoundTripGrid:
    """Dense BSM->IV round trip over 200 cells (5 vols x 5 moneyness x 4 tenors x call/put):
    exact sigma recovery (|iv_hat - sigma| < 1e-6) where the time value exceeds the $0.02 gate,
    NaN where it does not (deep ITM/OTM short-dated wings)."""

    def test_roundtrip_grid(self):
        sigmas = [0.05, 0.10, 0.25, 0.60, 1.20]
        moneyness = [0.8, 0.95, 1.0, 1.05, 1.25]  # S/K
        tenors = [1 / 365, 7 / 365, 45 / 365, 0.5]
        combos = list(itertools.product(sigmas, moneyness, tenors, (True, False)))
        n = len(combos)

        S0, r0, q0 = 100.0, 0.05, 0.01
        S = np.full(n, S0)
        K = np.array([S0 / m for (_s, m, _T, _c) in combos])
        T = np.array([T for (_s, _m, T, _c) in combos])
        sigma = np.array([s for (s, _m, _T, _c) in combos])
        is_call = np.array([c for (_s, _m, _T, c) in combos])
        r = np.full(n, r0)
        q = np.full(n, q0)

        price = bsm_price(S, K, T, r, q, sigma, is_call)
        iv_hat = compute_iv(price, S, K, T, r, q, is_call)

        price_floor = bsm_price(S, K, T, r, q, np.full(n, _MIN_SIGMA), is_call)
        time_value = price - price_floor
        identifiable = time_value > 0.02

        not_finite = np.where(identifiable & ~np.isfinite(iv_hat))[0]
        assert not_finite.size == 0, _fmt_cells(
            "identifiable cell returned NaN", combos, not_finite, iv_hat, time_value
        )
        err = np.abs(iv_hat - sigma)
        missed = np.where(identifiable & ~(err < 1e-6))[0]
        assert missed.size == 0, _fmt_cells(
            "identifiable cell failed |iv_hat - sigma| < 1e-6", combos, missed, iv_hat, err
        )
        ident = identifiable
        price_hat = bsm_price(
            S[ident], K[ident], T[ident], r[ident], q[ident], iv_hat[ident], is_call[ident]
        )
        assert (np.abs(price_hat - price[ident]) <= 1.5e-2).all()

        leaked = np.where(~identifiable & np.isfinite(iv_hat))[0]
        assert leaked.size == 0, _fmt_cells(
            "unidentifiable cell returned a value instead of NaN",
            combos,
            leaked,
            iv_hat,
            time_value,
        )
        assert identifiable.sum() >= 100


class TestIVIdentifiabilityGate:
    """extrinsic <= max(2 x $0.01, price_uncertainty) -> NaN. Prices are discounted intrinsic
    plus a controlled extrinsic, so the boundary is exact."""

    @staticmethod
    def _arrs(strike, is_call, extrinsic, T=7 / 365):
        S, K, Tv = np.array([100.0]), np.array([float(strike)]), np.array([T])
        r, q, c = np.array([0.05]), np.array([0.01]), np.array([is_call])
        floor = bsm_price(S, K, Tv, r, q, np.array([_MIN_SIGMA]), c)
        return floor + extrinsic, S, K, Tv, r, q, c

    def test_deep_itm_below_default_gate_is_nan(self):
        price, *a = self._arrs(80.0, True, extrinsic=0.019)
        assert np.isnan(compute_iv(price, *a)).all()
        price, *a = self._arrs(125.0, False, extrinsic=0.019)
        assert np.isnan(compute_iv(price, *a)).all()

    def test_extrinsic_above_gate_recovers_sigma(self):
        price, S, K, T, r, q, c = self._arrs(95.0, True, extrinsic=0.5, T=45 / 365)
        iv = compute_iv(price, S, K, T, r, q, c)
        assert np.isfinite(iv).all()
        assert np.abs(bsm_price(S, K, T, r, q, iv, c) - price).max() < 1.5e-2

    def test_price_uncertainty_widens_gate(self):
        # Extrinsic $0.04: above the default 2 x tol floor, below a $0.05 half-tick.
        price, *a = self._arrs(80.0, True, extrinsic=0.04)
        assert np.isfinite(compute_iv(price, *a)).all()
        assert np.isnan(compute_iv(price, *a, price_uncertainty=0.05)).all()

    def test_mask_agrees_with_compute_iv_nans(self):
        parts = [self._arrs(80.0, True, e) for e in (0.005, 0.019, 0.021, 0.5)]
        price, S, K, T, r, q, c = (np.concatenate([p[i] for p in parts]) for i in range(7))
        iv = compute_iv(price, S, K, T, r, q, c)
        mask = iv_unidentifiable_mask(price, S, K, T, r, q, c)
        assert (mask == np.isnan(iv)).all()
        assert mask.tolist() == [True, True, False, False]

    def test_mask_scalar_uncertainty_broadcasts(self):
        price, S, K, T, r, q, c = self._arrs(80.0, True, extrinsic=0.04)
        m_scalar = iv_unidentifiable_mask(price, S, K, T, r, q, c, 0.05)
        m_array = iv_unidentifiable_mask(price, S, K, T, r, q, c, np.array([0.05]))
        assert m_scalar.tolist() == m_array.tolist() == [True]

    def test_below_intrinsic_also_masked(self):
        price, S, K, T, r, q, c = self._arrs(80.0, True, extrinsic=-0.50)
        assert iv_unidentifiable_mask(price, S, K, T, r, q, c).all()
        assert np.isnan(compute_iv(price, S, K, T, r, q, c)).all()

    def test_bsm_extrinsic_matches_construction(self):
        price, S, K, T, r, q, c = self._arrs(80.0, True, extrinsic=0.25)
        assert bsm_extrinsic(price, S, K, T, r, q, c) == pytest.approx(0.25, abs=1e-12)
