"""SVI and SSVI. Ported from `volcalc` tests/test_surface.py at commit f53be83: the recovery of
a synthetic smile, the outlier filter, the eligibility rule, the penalty, the frozen residuals
and Jacobian (with _svi_reference.py) and the SSVI shape and theta.

Changed: the assertions on the leverage went with it. New: `butterfly`, with the example of
Axel Vogt that Gatheral and Jacquier (2014, section 3.1) give for a raw SVI with butterfly
arbitrage."""

import _svi_reference as frozen_svi  # tests/pricing is on sys.path (pytest prepend mode)
import numpy as np
import pytest

from s3t.pricing import ssvi, svi
from s3t.pricing.black76 import black_greeks

TAU = 30 / 365
VOGT = np.array([-0.0410, 0.1331, 0.3060, 0.3586, 0.4153])  # total variance: a, b, rho, m, sigma


def _chain(x: np.ndarray, tau: float, k: np.ndarray):
    iv = np.sqrt(svi.w_derivs(x, k)[0])
    absd = np.abs(black_greeks(100.0, 100 * np.exp(k), tau, 1.0, iv, k >= 0)["delta"])
    return iv, absd


def test_svi_recovers_synthetic_smile():
    x = np.array([0.02, 0.4, -0.5, 0.02, 0.1])
    k = np.linspace(-0.12, 0.08, 15)
    iv, absd = _chain(x, TAU, k)
    w = svi.w_derivs(svi.total(x, TAU), k)
    assert svi.g(k, *w).min() > 0 and svi.eligible(k, absd, 5, 2)
    f = svi.fit_slice(k, iv, np.full(k.size, 1e6), absd, TAU, None, 5, 2, 4.0, 3.0)
    assert f.status == "ok" and f.x is not None
    assert np.allclose(f.x, x, rtol=1e-6, atol=1e-8) and f.rmse < 1e-8
    assert f.used.all() and not f.outlier.any()
    kg = svi.grid(k, TAU * svi.w_derivs(x, 0.0)[0])
    assert svi.g(kg, *svi.w_derivs(svi.total(f.x, TAU), kg)).min() >= svi.G_TOL
    # Warm start from a wrong smile converges too; the density integrates to one.
    f2 = svi.fit_slice(k, iv, np.full(k.size, 1e6), absd, TAU, x * 1.5, 5, 2, 4.0, 3.0)
    assert f2.x is not None and np.allclose(f2.x, x, rtol=1e-5)
    kk = np.linspace(-3, 3, 20001)
    dens = svi.density(kk, *svi.w_derivs(svi.total(x, TAU), kk))
    assert np.trapezoid(dens, kk) == pytest.approx(1.0, abs=1e-6)


def test_svi_outlier_filter():
    x = np.array([0.02, 0.4, -0.5, 0.02, 0.1])
    k = np.linspace(-0.12, 0.08, 15)
    iv, absd = _chain(x, TAU, k)
    iv = iv + np.random.default_rng(0).normal(0, 1e-3, k.size)
    iv[7] += 0.05  # a 5 vol pt outlier
    f = svi.fit_slice(k, iv, np.full(k.size, 1e6), absd, TAU, None, 5, 2, 4.0, 3.0)
    assert f.status == "ok" and f.outlier.tolist() == [i == 7 for i in range(15)]
    assert not f.used[7] and f.rmse < 2e-3


def test_eligibility_rule():
    k = np.linspace(-0.1, 0.1, 9)
    absd = np.array([0.05, 0.1, 0.2, 0.3, 0.5, 0.3, 0.2, 0.1, 0.05])
    assert svi.eligible(k, absd, 5, 2)
    assert not svi.eligible(k[:6], absd[:6], 5, 2)  # one call-side wing strike
    assert not svi.eligible(k[2:7], absd[2:7], 5, 2)  # wings at |delta| >= 0.25
    assert not svi.eligible(np.repeat(k[:3], 3), np.repeat(absd[:3], 3), 5, 2)  # 3 strikes


def test_penalty_keeps_butterfly_arbitrage_out():
    # A frown (negative curvature) cannot be fitted arbitrage-free: the penalty bends it.
    k = np.linspace(-0.15, 0.15, 13)
    iv = 0.2 - 2.0 * k * k
    absd = np.abs(black_greeks(100.0, 100 * np.exp(k), TAU, 1.0, iv, k >= 0)["delta"])
    f = svi.fit_slice(k, iv, np.full(k.size, 1e6), absd, TAU, None, 5, 2, 4.0, 3.0)
    assert f.x is not None
    kg = svi.grid(k, TAU * 0.04)
    assert svi.g(kg, *svi.w_derivs(svi.total(f.x, TAU), kg)).min() >= svi.G_TOL


def test_stacked_penalty_gives_the_frozen_residuals_and_jacobian():
    # _jac takes x and its five stepped copies through one penalty evaluation. Residuals and
    # Jacobian have to keep the frozen loop's bits: an optimizer path follows them.
    rng = np.random.default_rng(5)
    k = np.linspace(-0.3, 0.2, 17)
    sw, iv = rng.uniform(1.0, 40.0, k.size), rng.uniform(0.1, 0.6, k.size)
    points = [
        np.array([0.02, 0.4, -0.6, 0.01, 0.1]),
        np.array([-0.03, 2.5, 0.999, -0.0, 1e-5]),  # arbitrage on the grid, rho and sigma at bounds
        np.array([0.5, 0.0, -0.999, -2.0, 5.0]),  # flat
        np.array([-25.0, 1e4, 0.3, 2.0, 0.7]),  # negative variance and the Lee bound
        svi.LO,
        svi.HI,
        *rng.uniform(svi.LO, np.minimum(svi.HI, [1.0, 5.0, 0.999, 2.0, 5.0]), (200, 5)),
    ]
    for x in points:
        for tau in (2e-5, 0.08, 1.4):
            args = (x, k, iv, sw, tau, svi.grid(k, tau * 0.04), 7.5)
            for fn, frozen in ((svi._jac, frozen_svi.jac), (svi._residuals, frozen_svi.residuals)):
                new, old = fn(*args), frozen(*args)
                assert new.shape == old.shape and new.tobytes() == old.tobytes(), (fn, x, tau)
    hit = frozen_svi.jac(points[1], k, iv, sw, 0.08, svi.grid(k, 0.0032), 7.5)[k.size :]
    assert np.any(hit != 0)  # the cases reach the penalty rows


def test_ssvi_recovers_shape_and_theta():
    shape = np.array([-0.6, 1.2, 0.35])
    thetas, taus = np.array([0.001, 0.003, 0.008, 0.02]), np.array([7, 21, 60, 150]) / 365
    parts = []
    for th, tau in zip(thetas, taus, strict=True):
        k = np.linspace(-4, 4, 21) * np.sqrt(th)
        parts.append(
            (k, np.sqrt(ssvi.w_derivs(th, shape, k)[0] / tau), np.full(21, th), np.full(21, tau))
        )
    k, iv, th, tau = (np.concatenate([p[i] for p in parts]) for i in range(4))
    fit = ssvi.fit_global(k, iv, np.full(k.size, 1e6), th, tau, None)
    assert fit is not None and np.allclose(fit, shape, atol=1e-6)
    assert fit[1] * (1 + abs(fit[0])) <= ssvi.ETA_MAX
    k1, iv1, _, _ = parts[1]
    th1 = ssvi.fit_theta(k1[::5], iv1[::5], np.full(5, 1e6), taus[1], shape)
    assert th1 == pytest.approx(thetas[1], rel=1e-6)
    assert ssvi.monotone(np.array([1.0, 0.5, 2.0, 1.5])).tolist() == [1.0, 1.0, 2.0, 2.0]
    w = ssvi.w_derivs(0.01, shape, np.linspace(-0.5, 0.5, 101))
    assert svi.g(np.linspace(-0.5, 0.5, 101), *w).min() > 0


# --- the butterfly check (new in WP-1.1) --------------------------------------------------------


def test_the_butterfly_check_fires_on_a_constructed_case():
    """Plan 3.4. Vogt's parameters fit a smile well and still have a negative density."""
    kg = np.linspace(-1.5, 1.5, svi.N_GRID)
    check = svi.butterfly(svi.smile(VOGT), kg)
    assert not check.ok and check.min_g < -0.01
    assert 0.5 < check.at < 1.2  # where the density is negative
    assert check.wing <= svi.LEE  # the wings are inside Lee's bound: it is g that fails
    dens = svi.density(kg, *svi.w_derivs(VOGT, kg))
    assert dens.min() < 0


def test_the_butterfly_check_passes_a_clean_smile_and_sees_steep_wings():
    x = svi.total(np.array([0.02, 0.4, -0.5, 0.02, 0.1]), TAU)
    kg = np.linspace(-0.5, 0.5, svi.N_GRID)
    check = svi.butterfly(svi.smile(x), kg)
    assert check.ok and check.min_g > 0
    steep = np.array([0.01, 1.5, 0.6, 0.0, 0.05])  # b (1 + rho) = 2.4, above Lee's bound
    wide = np.linspace(-3, 3, svi.N_GRID)
    assert not svi.butterfly(svi.smile(steep), wide).ok
    assert svi.butterfly(svi.smile(steep), wide).wing > svi.LEE
