"""Raw SVI for one expiration: the smile, the fit and the butterfly-arbitrage function (PRC-06).

Source: `volcalc` src/vrp/surface/svi.py at commit f53be83, with the rules unchanged: the fit
in IV space on annualized parameters, the penalty, the bounds, the seed, the consistency filter
and the eligibility rule.

Changed in the port (2026-11-27, WP-1.1):
- Not ported: the hat-matrix leverage (`leverage`, `H_MAX`, the field `h`). The source needs it
  for the leave-one-out sign of a trade print; nothing here signs prints.
- `butterfly` is new: the check of PRC-06 on any smile, with the wing bound.

From the source docstring: w(k) = a + b (rho (k - m) + sqrt((k - m)^2 + sigma^2)), k = ln(K/F),
w = sigma_BS^2 tau (Gatheral 2004; Gatheral & Jacquier 2014 for the arbitrage conditions).

The fit works on the annualized smile v = w / tau with the same parameters scaled (a' = a / tau,
b' = b / tau), so the parameter scale does not depend on tau (a 0DTE slice minutes before the
close has w ~ 1e-7); residuals are in IV space, sqrt(wt) (sqrt(v(k)) - iv), with wt =
1 / (sigma_noise^2 + sigma_s^2), sigma_s the IV-space half spread. Penalty residuals keep the
Gatheral-Jacquier butterfly function g(k) >= 0 on a grid spanning the strip, v > 0 there, and
the Lee bound b (1 + |rho|) <= 2 (a raw SVI slice is free of butterfly arbitrage iff g >= 0 and
the wings grow slower than 2|k|). Consistency filter: drop |iv - sigma_svi| > max(outlier_mad
MAD, outlier_volpts) and refit once.
"""

from collections.abc import Callable
from typing import NamedTuple

import numpy as np
from scipy.optimize import least_squares

Smile = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray]]  # k -> (w, w', w'')
PARAMS = ("a", "b", "rho", "m", "sigma")
LEE = 2.0  # Lee moment bound on the total-variance wing slopes b (1 +- rho)
PENALTY = 1.0  # g violation of 0.01 costs a 1 vol pt data error (times the median weight)
G_TOL = -1e-3  # a solution with min g below this is a failed fit
N_GRID = 61
MAX_NFEV = 200
FD_STEP = 1e-7  # finite-difference step of the penalty Jacobian
W_MIN = 1e-12
WING_DELTA = 0.25  # wing strikes are those beyond |delta| = 0.25
LO = np.array([-25.0, 0.0, -0.999, -2.0, 1e-5])  # annualized a', b', rho, m, sigma
HI = np.array([25.0, 1e4, 0.999, 2.0, 5.0])


class Fit(NamedTuple):
    x: np.ndarray  # annualized parameters (a', b', rho, m, sigma)
    cost: float  # least_squares cost (half the squared residual norm) over the used points


class SliceFit(NamedTuple):
    status: str  # "ok" | "fail"
    x: np.ndarray | None  # annualized parameters
    used: np.ndarray  # points in the final fit
    outlier: np.ndarray  # points dropped by the consistency filter
    e: np.ndarray  # iv - sigma_svi(k) (NaN without a fit)
    rmse: float  # unweighted RMS of e over the used points (IV units)


def w_derivs(x, k):
    """Total variance w and its first two k-derivatives at the parameters x = (a, b, rho, m,
    sigma); every element broadcasts."""
    a, b, rho, m, sigma = x
    dk = k - m
    R = np.sqrt(dk * dk + sigma * sigma)
    return a + b * (rho * dk + R), b * (rho + dk / R), b * sigma * sigma / R**3


def smile(x_total: np.ndarray) -> Smile:
    """k -> (w, w', w'') for total-variance parameters."""
    return lambda k: w_derivs(x_total, np.asarray(k, dtype=np.float64))


def total(x: np.ndarray, tau: float) -> np.ndarray:
    """Annualized parameters -> total-variance parameters (a, b, rho, m, sigma)."""
    return np.array([x[0] * tau, x[1] * tau, x[2], x[3], x[4]])


def g(k, w, wp, wpp):
    """Gatheral-Jacquier butterfly function; the slice is free of butterfly arbitrage iff g >= 0
    (and the Lee bound holds)."""
    w = np.maximum(w, W_MIN)
    return (1 - k * wp / (2 * w)) ** 2 - wp * wp / 4 * (1 / w + 0.25) + wpp / 2


def density(k, w, wp, wpp):
    """Breeden-Litzenberger density of the log-moneyness ln(S_T / F) implied by the smile (GJ
    2014 eq. 2.2): g(k) / sqrt(2 pi w) exp(-d2^2 / 2)."""
    w = np.maximum(w, W_MIN)
    d2 = -k / np.sqrt(w) - np.sqrt(w) / 2
    return g(k, w, wp, wpp) / np.sqrt(2 * np.pi * w) * np.exp(-d2 * d2 / 2)


def eligible(k: np.ndarray, abs_delta: np.ndarray, min_strikes: int, min_wing: int) -> bool:
    """Eligible for an own fit: >= min_strikes distinct strikes with >= min_wing beyond
    |delta| = 0.25 on each side (k < 0 puts, k > 0 calls)."""
    wing = abs_delta < WING_DELTA
    return (
        np.unique(k).size >= min_strikes
        and int(np.sum(wing & (k < 0))) >= min_wing
        and int(np.sum(wing & (k > 0))) >= min_wing
    )


def seed(k: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Moment seed: a parabola through (k, v) read as the SVI expansion about m = 0,
    v ~ (a' + b' sigma) + b' rho k + b' k^2 / (2 sigma)."""
    sig = max(float(np.std(k)), 1e-4)
    c2, c1, c0 = np.polyfit(k, v, 2) if np.unique(k).size >= 3 else (0.0, 0.0, float(v.mean()))
    b = max(2 * c2 * sig, 0.1 * float(v.mean()) / sig)
    rho = float(np.clip(c1 / b, -0.9, 0.9))
    a = c0 - b * sig
    floor = -b * sig * np.sqrt(1 - rho * rho)
    if a <= floor:
        a = floor + 0.5 * float(v.min())
    return np.clip(np.array([a, b, rho, 0.0, sig]), LO, HI)


def _penalty(x, tau, kg, scale):
    """Penalty residuals on the grid: g < 0, v < 0 and the Lee bound, scaled to the data weights
    (a g violation of 0.01 costs as much as a 1 vol pt error on a typical point). x is one
    parameter vector or a stack of them, one row of residuals each."""
    p = np.moveaxis(x, -1, 0)[..., None]
    vg, vpg, vppg = w_derivs(p, kg)
    gg = g(kg, tau * vg, tau * vpg, tau * vppg)
    lee = np.maximum(tau * p[1] * (1 + np.abs(p[2])) - LEE, 0.0)
    return scale * np.concatenate([np.minimum(gg, 0.0), np.minimum(vg, 0.0) / tau, lee], axis=-1)


def _residuals(x, k, iv, sw, tau, kg, scale):
    v = w_derivs(x, k)[0]
    data = sw * (np.sqrt(np.maximum(v, W_MIN)) - iv)
    return np.concatenate([data, _penalty(x, tau, kg, scale)])


def _jac_data(x, k, sw):
    """Analytic Jacobian of the data residuals sw (sqrt(v(k)) - iv) wrt (a', b', rho, m, sigma)."""
    a, b, rho, m, sigma = x
    dk = k - m
    R = np.sqrt(dk * dk + sigma * sigma)
    iv = np.sqrt(np.maximum(a + b * (rho * dk + R), W_MIN))
    dv = np.column_stack(
        [np.ones_like(k), rho * dk + R, b * dk, -b * (rho + dk / R), b * sigma / R]
    )
    return sw[:, None] * dv / (2 * iv)[:, None]


def _jac(x, k, iv, sw, tau, kg, scale):
    """Analytic on the data rows; forward differences on the penalty rows, with x and its five
    stepped copies in one penalty evaluation."""
    h = FD_STEP * np.maximum(1.0, np.abs(x))
    xs = np.tile(x, (x.size + 1, 1))
    xs[np.arange(1, x.size + 1), np.arange(x.size)] += h
    p = _penalty(xs, tau, kg, scale)
    return np.vstack([_jac_data(x, k, sw), ((p[1:] - p[0]) / h[:, None]).T])


def fit(k, iv, wt, tau: float, seeds: list[np.ndarray], kgrid: np.ndarray) -> Fit | None:
    """Weighted IV-space fit from the seeds in turn, the first that converges to a finite,
    arbitrage-free (g >= G_TOL, v > 0 on kgrid) smile wins; None when none does."""
    sw = np.sqrt(wt)
    scale = PENALTY * float(np.median(sw))
    for x0 in seeds:
        try:
            res = least_squares(
                _residuals,
                np.clip(x0, LO, HI),
                jac=_jac,  # type: ignore[arg-type]  # scipy stub types jac as str only
                bounds=(LO, HI),
                args=(k, iv, sw, tau, kgrid, scale),
                x_scale="jac",
                max_nfev=MAX_NFEV,
            )
        except (ValueError, np.linalg.LinAlgError):
            continue
        if not np.all(np.isfinite(res.x)):
            continue
        vg, vpg, vppg = w_derivs(res.x, kgrid)
        if vg.min() <= 0 or g(kgrid, tau * vg, tau * vpg, tau * vppg).min() < G_TOL:
            continue
        return Fit(res.x, float(res.cost))
    return None


def grid(k: np.ndarray, w_atm: float) -> np.ndarray:
    """Penalty / strip grid: the observed k range or |z| <= 8 around the money, whichever is
    wider, so the surface is arbitrage-free where it is integrated."""
    s = 8 * np.sqrt(max(w_atm, W_MIN))
    return np.linspace(min(k.min(), -s), max(k.max(), s), N_GRID)


def fit_slice(
    k: np.ndarray,
    iv: np.ndarray,
    wt: np.ndarray,
    abs_delta: np.ndarray,
    tau: float,
    warm: np.ndarray | None,
    min_strikes: int,
    min_wing: int,
    outlier_mad: float,
    outlier_volpts: float,
) -> SliceFit:
    """Fit an eligible slice (caller checks `eligible`): from the warm start, else the moment
    seed; the consistency filter drops outliers and refits once when the rest is still
    eligible."""
    v = iv * iv
    atm = float(v[np.argmin(np.abs(k))]) * tau
    kg = grid(k, atm)
    seeds = ([warm] if warm is not None else []) + [seed(k, v)]
    n = k.size
    used, outlier = np.ones(n, dtype=bool), np.zeros(n, dtype=bool)
    nan = np.full(n, np.nan)
    f = fit(k, iv, wt, tau, seeds, kg)
    if f is None:
        return SliceFit("fail", None, used, outlier, nan, np.nan)
    e = iv - np.sqrt(np.maximum(w_derivs(f.x, k)[0], W_MIN))
    mad = float(np.median(np.abs(e - np.median(e))))
    outlier = np.abs(e) > max(outlier_mad * mad, outlier_volpts / 100)
    if outlier.any():
        keep = ~outlier
        if eligible(k[keep], abs_delta[keep], min_strikes, min_wing):
            f2 = fit(k[keep], iv[keep], wt[keep], tau, [f.x], kg)
            if f2 is not None:
                f, used = f2, keep
                e = iv - np.sqrt(np.maximum(w_derivs(f.x, k)[0], W_MIN))
    rmse = float(np.sqrt(np.mean(e[used] ** 2)))
    return SliceFit("ok", f.x, used, outlier & ~used, e, rmse)


class Butterfly(NamedTuple):
    ok: bool  # positive variance, g >= G_TOL on the grid and wings inside the Lee bound
    min_g: float  # the lowest g on the grid
    at: float  # the log-moneyness where it is lowest
    wing: float  # the steeper of the two wing slopes |w'| at the grid's ends; the bound is LEE


def butterfly(sm: Smile, kgrid: np.ndarray) -> Butterfly:
    """The butterfly-arbitrage check of a smile on a grid (new in WP-1.1, PRC-06). It is the
    test the fit applies to its own solution, made for any smile: an SSVI slice, a slice between
    two expirations. A smile that fails it has a negative density somewhere near `at`."""
    w, wp, wpp = sm(kgrid)
    gg = g(kgrid, w, wp, wpp)
    i = int(np.argmin(gg))
    wing = float(max(abs(wp[0]), abs(wp[-1])))
    ok = bool(w.min() > 0 and gg[i] >= G_TOL and wing <= LEE)
    return Butterfly(ok, float(gg[i]), float(kgrid[i]), wing)
