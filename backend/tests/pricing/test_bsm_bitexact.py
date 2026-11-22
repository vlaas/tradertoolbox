"""Port of `volcalc` tests/test_bsm_bitexact.py at commit f53be83 (itself a port of bt_dataset
tests/test_greeks_bitexact.py): the BSM kernels and the active-set IV solver in
s3t/pricing/bsm.py must be BIT-IDENTICAL to the frozen pre-WP1 reference in
tests/pricing/_iv_reference.py (scipy.stats.norm based, 50 fixed Newton iterations). Every
comparison is on the uint64 view of the float64 arrays, so NaN payloads and signed zeros count
too.

Not ported: test_bsm_greeks_bitwise and test_bsm_greeks_nan_inputs, with bsm_greeks."""

import _iv_reference as ref  # tests/ is on sys.path (pytest prepend mode)
import numpy as np
import pytest
from scipy.stats import _continuous_distns, norm

from s3t.pricing import bsm as greeks


def _bits(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(np.asarray(a, dtype=np.float64)).view(np.uint64)


def assert_bitwise(a, b, what=""):
    a, b = _bits(a), _bits(b)
    assert a.shape == b.shape, what
    if not np.array_equal(a, b):
        idx = np.flatnonzero(a != b)
        fa = a.view(np.float64)[idx[:5]]
        fb = b.view(np.float64)[idx[:5]]
        pytest.fail(f"{what}: {idx.size} of {a.size} values differ; first {fa} vs {fb}")


_SWEEP = np.concatenate(
    [
        np.linspace(-40.0, 40.0, 200_001),
        np.array(
            [
                0.0,
                -0.0,
                np.inf,
                -np.inf,
                np.nan,
                8.3,
                -8.3,
                37.5,
                -37.5,
                38.5,
                -38.5,
                1e-300,
                -1e-300,
                5e-324,
            ]
        ),  # fmt: skip
        np.random.default_rng(7).standard_normal(300_000) * 3.0,
    ]
)


def test_ndtr_is_norm_cdf_bitwise():
    assert_bitwise(greeks._N(_SWEEP), norm.cdf(_SWEEP), "ndtr vs norm.cdf")


def test_npdf_is_norm_pdf_bitwise():
    assert_bitwise(greeks._n(_SWEEP), norm.pdf(_SWEEP), "pdf vs norm.pdf")
    assert greeks._SQRT_2PI == _continuous_distns._norm_pdf_C


def _random_inputs(n: int, seed: int):
    g = np.random.default_rng(seed)
    S = g.uniform(5.0, 6000.0, n)
    K = S * np.exp(g.normal(0.0, 0.25, n))
    T = np.concatenate([g.uniform(1e-7, 2.5, n - n // 10), np.full(n // 10, greeks._MIN_T)])
    g.shuffle(T)
    r = g.uniform(-0.02, 0.15, n)
    q = g.uniform(0.0, 0.3, n)
    sigma = np.concatenate(
        [
            g.uniform(0.01, 3.0, n - 2 * (n // 20)),
            np.full(n // 20, greeks._MIN_SIGMA),
            np.full(n // 20, greeks._MAX_SIGMA),
        ]
    )
    g.shuffle(sigma)
    is_call = g.random(n) < 0.5
    return S, K, T, r, q, sigma, is_call


class TestKernels:
    def test_bsm_price_bitwise(self):
        S, K, T, r, q, sigma, c = _random_inputs(100_000, 1)
        assert_bitwise(
            greeks.bsm_price(S, K, T, r, q, sigma, c), ref.bsm_price(S, K, T, r, q, sigma, c)
        )

    def test_vega_only_bitwise(self):
        S, K, T, r, q, sigma, c = _random_inputs(100_000, 2)
        assert_bitwise(
            greeks._bsm_vega_only(S, K, T, r, q, sigma), ref._bsm_vega_only(S, K, T, r, q, sigma)
        )

    def test_fused_price_and_vega_matches_separate_calls(self):
        S, K, T, r, q, sigma, c = _random_inputs(100_000, 5)
        T_safe = np.maximum(T, greeks._MIN_T)
        inv = greeks._newton_invariants(S, K, T_safe, r, q)
        p, v = greeks._bsm_price_and_vega(inv, sigma, c)
        assert_bitwise(p, ref.bsm_price(S, K, T_safe, r, q, sigma, c), "fused price")
        assert_bitwise(v, ref._bsm_vega_only(S, K, T_safe, r, q, sigma), "fused vega")


def _iv_batch(seed: int, n: int = 60_000):
    """Prices spanning: cleanly convergent rows, rows pinned at the clip bounds, near-intrinsic
    rows (gate + bisection territory), unphysical rows (outside the bracket), NaN inputs,
    mixed calls/puts."""
    g = np.random.default_rng(seed)
    S, K, T, r, q, sigma_true, c = _random_inputs(n, seed + 100)
    price = ref.bsm_price(S, K, T, r, q, sigma_true, c)
    k = n // 20
    sl = slice(0, k)
    lo = ref.bsm_price(S[sl], K[sl], T[sl], r[sl], q[sl], np.full(k, greeks._MIN_SIGMA), c[sl])
    sl = slice(k, 2 * k)
    hi = ref.bsm_price(S[sl], K[sl], T[sl], r[sl], q[sl], np.full(k, greeks._MAX_SIGMA), c[sl])
    price[:k] = lo + 0.0099  # pinned near the lower clip bound
    price[k : 2 * k] = hi + 0.0099  # pinned near the upper clip bound
    sl = slice(2 * k, 3 * k)
    price[sl] = ref.bsm_price(
        S[sl], K[sl], T[sl], r[sl], q[sl], np.full(k, greeks._MIN_SIGMA), c[sl]
    ) + g.uniform(0.0, 0.03, k)  # gate edge
    price[3 * k : 4 * k] = -1.0  # below bracket -> NaN
    price[4 * k : 5 * k] = 1e9  # above bracket -> NaN
    S[5 * k : 5 * k + 50] = np.nan  # NaN inputs
    pu = np.where(price < 3.0, 0.05, 0.10) / 2.0
    return price, S, K, T, r, q, c, pu


class TestComputeIvBitwise:
    @pytest.mark.parametrize("seed", [11, 12, 13])
    def test_compute_iv_bitwise_random_batches(self, seed):
        price, S, K, T, r, q, c, pu = _iv_batch(seed)
        assert_bitwise(
            greeks.compute_iv(price, S, K, T, r, q, c),
            ref.compute_iv(price, S, K, T, r, q, c),
            f"compute_iv seed={seed}",
        )
        assert_bitwise(
            greeks.compute_iv(price, S, K, T, r, q, c, price_uncertainty=pu),
            ref.compute_iv(price, S, K, T, r, q, c, price_uncertainty=pu),
            f"compute_iv+pu seed={seed}",
        )
        assert_bitwise(
            greeks.compute_iv(price, S, K, T, r, q, c, price_uncertainty=0.025),
            ref.compute_iv(price, S, K, T, r, q, c, price_uncertainty=0.025),
            f"compute_iv+scalar pu seed={seed}",
        )

    def test_short_dated_far_otm_oscillators(self):
        # Very short T, far OTM: Newton can limit-cycle; both must run the same 50 updates.
        g = np.random.default_rng(21)
        n = 20_000
        S = np.full(n, 5000.0)
        K = S * np.exp(g.uniform(0.02, 0.08, n) * np.where(g.random(n) < 0.5, 1, -1))
        T = g.uniform(greeks._MIN_T, 1.0 / 365.0, n)
        r = np.full(n, 0.045)
        q = np.zeros(n)
        c = K > S
        price = ref.bsm_price(S, K, T, r, q, g.uniform(0.05, 1.5, n), c) + g.uniform(0.0, 0.05, n)
        assert_bitwise(
            greeks.compute_iv(price, S, K, T, r, q, c),
            ref.compute_iv(price, S, K, T, r, q, c),
            "oscillators",
        )

    def _benign(self, n: int, seed: int):
        """Rows Newton solves to well under 1e-8: near the money, weeks to a year out."""
        g = np.random.default_rng(seed)
        S = g.uniform(50.0, 500.0, n)
        K = S * np.exp(g.normal(0.0, 0.03, n))
        T = g.uniform(0.05, 1.0, n)
        r, q, c = np.full(n, 0.04), np.full(n, 0.01), g.random(n) < 0.5
        return ref.bsm_price(S, K, T, r, q, g.uniform(0.15, 0.6, n), c), S, K, T, r, q, c

    def test_blocks_stop_together_when_every_row_converges(self):
        # More than one block, and the batch-wide exit test passes: every block must stop at the
        # iteration where the worst row of the whole batch got within 1e-8.
        n = 3 * greeks._NEWTON_BLOCK + 5
        price, S, K, T, r, q, c = self._benign(n, 51)
        inv = greeks._newton_invariants(S, K, np.maximum(T, greeks._MIN_T), r, q)
        _, worst = greeks._newton(inv, price, c, 50)
        assert 0 < np.flatnonzero(worst < 1e-8)[0] < 50
        iv = greeks.compute_iv(price, S, K, T, r, q, c)
        assert np.isfinite(iv).all()
        assert_bitwise(iv, ref.compute_iv(price, S, K, T, r, q, c), "all rows converge")

    def test_oscillators_in_the_last_block_keep_every_block_running(self):
        n, k = 2 * greeks._NEWTON_BLOCK + 17, 17
        price, S, K, T, r, q, c = self._benign(n, 52)
        g = np.random.default_rng(53)
        S[-k:] = 5000.0
        K[-k:] = 5000.0 * np.exp(g.uniform(0.02, 0.08, k))
        T[-k:] = g.uniform(greeks._MIN_T, 1.0 / 365.0, k)
        c[-k:] = True
        price[-k:] = ref.bsm_price(
            S[-k:], K[-k:], T[-k:], r[-k:], q[-k:], g.uniform(0.05, 1.5, k), c[-k:]
        ) + g.uniform(0.0, 0.05, k)
        assert_bitwise(
            greeks.compute_iv(price, S, K, T, r, q, c),
            ref.compute_iv(price, S, K, T, r, q, c),
            "oscillators in the last block",
        )

    def test_batch_smaller_than_a_block(self):
        price, S, K, T, r, q, c, pu = _iv_batch(61, n=greeks._NEWTON_BLOCK // 4)
        assert_bitwise(
            greeks.compute_iv(price, S, K, T, r, q, c, price_uncertainty=pu),
            ref.compute_iv(price, S, K, T, r, q, c, price_uncertainty=pu),
            "less than a block",
        )

    def test_all_invalid_early_return(self):
        price = np.array([-1.0, 1e9, np.nan])
        S = np.full(3, 100.0)
        K = np.full(3, 100.0)
        T = np.full(3, 0.1)
        r = np.zeros(3)
        q = np.zeros(3)
        c = np.array([True, False, True])
        assert_bitwise(
            greeks.compute_iv(price, S, K, T, r, q, c),
            ref.compute_iv(price, S, K, T, r, q, c),
            "all invalid",
        )

    def test_return_details_matches_public_mask(self):
        price, S, K, T, r, q, c, pu = _iv_batch(31)
        res = greeks.compute_iv(price, S, K, T, r, q, c, price_uncertainty=pu, return_details=True)
        assert_bitwise(res.iv, ref.compute_iv(price, S, K, T, r, q, c, price_uncertainty=pu), "iv")
        assert np.array_equal(
            res.unidentifiable, ref.iv_unidentifiable_mask(price, S, K, T, r, q, c, pu)
        )
        assert_bitwise(res.extrinsic, ref.bsm_extrinsic(price, S, K, T, r, q, c), "extrinsic")

    def test_mask_and_extrinsic_public_functions_unchanged(self):
        price, S, K, T, r, q, c, pu = _iv_batch(41)
        assert np.array_equal(
            greeks.iv_unidentifiable_mask(price, S, K, T, r, q, c, pu),
            ref.iv_unidentifiable_mask(price, S, K, T, r, q, c, pu),
        )
        assert_bitwise(
            greeks.bsm_extrinsic(price, S, K, T, r, q, c),
            ref.bsm_extrinsic(price, S, K, T, r, q, c),
            "bsm_extrinsic",
        )
